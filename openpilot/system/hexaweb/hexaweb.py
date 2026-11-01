#!/usr/bin/env python3
"""Hexa phone panel: a small local web page to see the comma and change the fork's options.

Runs only while the "painel no celular" option (HexaWebEnabled) is on. Security rules live in
openpilot/selfdrive/hexa/phone.py; this file only parses HTTP and calls PanelBackend.
"""
import gzip
import json
import math
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from openpilot.selfdrive.hexa import live as hexa_live
from openpilot.selfdrive.hexa import marks as hexa_marks
from openpilot.selfdrive.hexa import radars
from openpilot.selfdrive.hexa import phone
from openpilot.selfdrive.hexa import timeline
from openpilot.selfdrive.hexa import updates
from openpilot.selfdrive.hexa.options import GROUPS, OPTIONS, get_option, supported
from openpilot.selfdrive.hexa.phone import VehicleState

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 4096
STATIC_FILES = {
  "/": ("index.html", "text/html; charset=utf-8"),
  "/index.html": ("index.html", "text/html; charset=utf-8"),
  "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
  "/replay": ("replay.html", "text/html; charset=utf-8"),
  "/viagens": ("viagens.html", "text/html; charset=utf-8"),
}
REPLAY_CACHE = 3
TRIP_CACHE = 120  # minutes of trips kept in memory
TRIP_INDEX_S = 30.  # the page asks once per minute of the trip: don't walk the log folder every time
GZIP_MIN_BYTES = 16 * 1024  # segments kept in memory
SERVE_RETRY = 5.  # s between tries when the port can't be opened
SECURITY_HEADERS = {
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "no-referrer",
  "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:",
}


class PanelBackend:
  def __init__(self, store, vehicle_state: Callable[[], VehicleState],
               clock: Callable[[], float] = phone.wall_time, mono: Callable[[], float] = time.monotonic,
               log_event: Callable[[dict], None] | None = None, log_root: str | None = None,
               signal_updater: Callable[[str], None] = updates.signal_updater,
               live_state: Callable[[], dict] | None = None, radar_path: Path | None = None,
               gps_fix: Callable[[], tuple[float, float, float, float] | None] | None = None):
    self.store = store  # Params, or anything with get/put/get_bool/put_bool/remove
    self._vehicle_state = vehicle_state
    self.clock = clock  # wall clock: token, audit and mark dates
    self.mono = mono  # monotonic clock, same as logMonoTime: pairing code expiry, bookmark matching
    self.log_event = log_event or (lambda fields: None)  # writes a 'hexa_mark' event to the log
    self.lock = threading.Lock()
    self._vs_lock = threading.Lock()  # requests run in threads; SubMaster is not thread safe
    self.pin_failures = 0
    self._last_mark: dict[str, float] = {}  # token id -> monotonic time of the last phone mark
    self.log_root = log_root
    self._replay_cache: OrderedDict[str, dict] = OrderedDict()
    self._replay_lock = threading.Lock()
    self._trip_cache: OrderedDict[str, dict] = OrderedDict()
    self._trip_index: tuple[float, list[dict]] = (-TRIP_INDEX_S, [])
    self.signal_updater = signal_updater
    self.radar_path = radar_path
    self._radar_job = {"busy": False, "error": ""}
    self._radar_rows: tuple[tuple, object] = ((), None)
    self._gps_fix = gps_fix or (lambda: None)
    self._radar_pins: dict[str, tuple] = {}
    self._live_state = live_state or dict

  def vehicle_state(self) -> VehicleState:
    with self._vs_lock:
      try:
        return self._vehicle_state()
      except Exception:
        return VehicleState(onroad=True)  # unknown: treat as driving, read only

  # *** helpers ***

  def _tokens(self) -> list[dict]:
    return self.store.get(phone.TOKENS_PARAM) or []

  def _audit(self, entry: dict) -> None:
    self.store.put(phone.AUDIT_PARAM, phone.add_audit(self.store.get(phone.AUDIT_PARAM), entry, self.clock()))

  def _value(self, opt):
    if opt.kind == "bool":
      return bool(self.store.get_bool(opt.key))
    v = self.store.get(opt.key)
    return opt.default if v is None else int(v)

  def live(self) -> dict:
    with self._vs_lock:
      try:
        return self._live_state()
      except Exception:
        return {}

  # *** API ***

  def authenticate(self, token: str | None) -> dict | None:
    with self.lock:
      return phone.find_token(self._tokens(), token)

  def pair(self, pin: str, name: str) -> tuple[int, dict]:
    with self.lock:
      stored = self.store.get(phone.PIN_PARAM)
      if phone.parse_pin(stored, self.mono()) is None:
        return 403, {"error": "Gere um código no comma: Configurações → hexa → parear celular."}
      if not phone.pin_matches(stored, pin, self.mono()):
        self.pin_failures += 1
        if self.pin_failures >= phone.MAX_PIN_FAILURES:
          self.store.remove(phone.PIN_PARAM)
          self.pin_failures = 0
          self._audit({"event": "pin_locked"})
          return 403, {"error": "Código errado várias vezes. Gere um novo código no comma."}
        return 403, {"error": "Código errado."}
      self.pin_failures = 0
      self.store.remove(phone.PIN_PARAM)  # single use
      token, tokens = phone.issue_token(self._tokens(), name, self.clock())
      self.store.put(phone.TOKENS_PARAM, tokens)
      self._audit({"event": "paired", "who": tokens[-1]["name"]})
      return 200, {"token": token, "name": tokens[-1]["name"]}

  def state(self, who: dict) -> dict:
    vs = self.vehicle_state()
    groups = []
    with self.lock:
      for gkey, gtitle in GROUPS:
        items = []
        for opt in OPTIONS:
          if opt.group != gkey or not supported(opt, self.store):
            continue
          writable, reason = phone.can_write(opt, vs)
          items.append({
            "key": opt.key, "title": opt.title, "description": opt.description, "kind": opt.kind,
            "choices": list(opt.choices), "value": self._value(opt), "writable": writable, "reason": reason,
            "restart_needed": opt.restart_needed,
          })
        if items:
          groups.append({"key": gkey, "title": gtitle, "options": items})
      audit = (self.store.get(phone.AUDIT_PARAM) or [])[-20:]
      return {
        "device": {
          "branch": self.store.get("GitBranch") or "",
          "commit": (self.store.get("GitCommit") or "")[:7],
          "version": self.store.get("Version") or "",
        },
        "vehicle": {"onroad": vs.onroad, "parked": vs.parked, "standstill": vs.standstill, "engaged": vs.engaged},
        "groups": groups,
        "audit": list(reversed(audit)),
        "titles": {o.key: o.title for o in OPTIONS},
        "you": who.get("name", ""),
      }

  def set_option(self, who: dict, key: str, value) -> tuple[int, dict]:
    opt = get_option(key)
    if opt is None or not supported(opt, self.store):
      return 404, {"error": "Opção desconhecida."}
    try:
      value = opt.coerce(value)
    except ValueError as e:
      return 400, {"error": str(e)}
    vs = self.vehicle_state()
    with self.lock:
      ok, reason = phone.can_write(opt, vs)
      old = self._value(opt)
      if not ok:
        self._audit({"event": "denied", "who": who.get("name", ""), "key": key, "new": value, "reason": reason})
        return 409, {"error": reason}
      if opt.kind == "bool":
        self.store.put_bool(key, value)
      else:
        self.store.put(key, value)
      self._audit({"event": "set", "who": who.get("name", ""), "key": key, "old": old, "new": value})
      msg = "Vale na próxima partida do carro." if opt.restart_needed else "Vale no próximo engate."
      return 200, {"ok": True, "message": msg}

  # *** marks ***

  def _marks(self) -> list[dict]:
    return self.store.get(hexa_marks.MARKS_PARAM) or []

  def _custom_reasons(self) -> list[dict]:
    return self.store.get(hexa_marks.REASONS_PARAM) or []

  def marks_state(self) -> dict:
    with self.lock:
      marks = self._marks()
      return {
        "reasons": hexa_marks.reasons(self._custom_reasons()),
        "marks": [hexa_marks.public(m) for m in reversed(marks[-30:])],
        "pending": sum(1 for m in marks if not m.get("reason")),
      }

  def export_marks(self) -> dict:
    with self.lock:
      return {"exported_at": int(self.clock()), "marks": [hexa_marks.public(m) for m in self._marks()]}

  def mark(self, who: dict, reason: str, note: str = "", delay=0.) -> tuple[int, dict]:
    """Phone mark: always allowed (it doesn't change how the car drives), at most one per MIN_MARK_INTERVAL."""
    now_mono = self.mono()
    with self.lock:
      label = hexa_marks.reason_label(self._custom_reasons(), reason)
      if label is None:
        return 400, {"error": "Motivo desconhecido."}
      last = self._last_mark.get(who.get("id", ""))
      if last is not None and now_mono - last < hexa_marks.MIN_MARK_INTERVAL:
        return 429, {"error": "Espere um instante entre marcas."}
      self._last_mark[who.get("id", "")] = now_mono
      delay = hexa_marks.clean_delay(delay)
      mark = hexa_marks.new_mark("celular", who=who.get("name", ""), reason=reason, label=label,
                                 note=hexa_marks.clean_note(note), now=self.clock() - delay)
      mark["delay_s"] = delay
      mark["_req_mono_ns"] = int(now_mono * 1e9)
      self.store.put(hexa_marks.MARKS_PARAM, hexa_marks.append(self._marks(), mark))
      self.store.put_bool(hexa_marks.BOOKMARK_REQUEST_PARAM, True)  # the comma raises its own bookmark
    self.log_event(hexa_marks.log_fields(mark))
    return 200, {"ok": True, "mark": hexa_marks.public(mark)}

  def classify(self, who: dict, mark_id: str, reason: str, note: str = "", replace_note: bool = False) -> tuple[int, dict]:
    with self.lock:
      label = hexa_marks.reason_label(self._custom_reasons(), reason)
      if label is None:
        return 400, {"error": "Motivo desconhecido."}
      try:
        marks, mark = hexa_marks.set_reason(self._marks(), mark_id, reason, label, note, replace_note)
      except KeyError:
        return 404, {"error": "Marca não encontrada."}
      mark = dict(mark, who=mark.get("who") or who.get("name", ""))
      marks = [mark if m.get("id") == mark_id else m for m in marks]
      self.store.put(hexa_marks.MARKS_PARAM, marks)
    self.log_event(hexa_marks.log_fields(mark))
    return 200, {"ok": True, "mark": hexa_marks.public(mark)}

  def on_bookmark(self, mono_ns: int) -> dict:
    """A bookmark showed up in the log (comma flag, or the one a phone mark asked for)."""
    with self.lock:
      marks, mark, created = hexa_marks.attach_bookmark(self._marks(), mono_ns)
      self.store.put(hexa_marks.MARKS_PARAM, marks)
    if not created:
      self.log_event(hexa_marks.log_fields(mark))  # now with the bookmark's exact log time
    return mark

  def add_reason(self, who: dict, label: str) -> tuple[int, dict]:
    with self.lock:
      try:
        custom, reason = hexa_marks.add_reason(self._custom_reasons(), label)
      except ValueError as e:
        return 400, {"error": str(e)}
      self.store.put(hexa_marks.REASONS_PARAM, custom)
      self._audit({"event": "reason_added", "who": who.get("name", ""), "key": reason["key"], "new": reason["label"]})
    return 200, {"ok": True, "reason": reason}

  def remove_reason(self, who: dict, key: str) -> tuple[int, dict]:
    with self.lock:
      custom = self._custom_reasons()
      if not any(c.get("key") == key for c in custom):
        return 404, {"error": "Só dá para remover os motivos criados por você."}
      self.store.put(hexa_marks.REASONS_PARAM, hexa_marks.remove_reason(custom, key))
      self._audit({"event": "reason_removed", "who": who.get("name", ""), "key": key})
    return 200, {"ok": True}

  # *** replay ***

  def replay_list(self) -> tuple[int, dict]:
    if not self.log_root:
      return 404, {"error": "Sem pasta de logs."}
    segs = timeline.segment_files(self.log_root)
    return 200, {"segments": [{"id": s["id"], "label": s["id"]} for s in segs]}

  def replay_data(self, seg_id: str) -> tuple[int, dict]:
    """Reading a log takes CPU: only with the car off, never while driving."""
    if self.vehicle_state().onroad:
      return 409, {"error": "O replay só abre com o carro desligado."}
    if not self.log_root:
      return 404, {"error": "Sem pasta de logs."}
    files = {s["id"]: s["file"] for s in timeline.segment_files(self.log_root, limit=10_000)}
    if seg_id not in files:  # only ids from the list: no paths from the outside
      return 404, {"error": "Trecho não encontrado."}
    with self._replay_lock:
      if seg_id not in self._replay_cache:
        try:
          self._replay_cache[seg_id] = timeline.build(timeline.read_rlog(files[seg_id]))
        except Exception as e:
          return 500, {"error": f"Não foi possível ler o trecho: {type(e).__name__}"}
        while len(self._replay_cache) > REPLAY_CACHE:
          self._replay_cache.popitem(last=False)
      self._replay_cache.move_to_end(seg_id)
      return 200, self._replay_cache[seg_id]

  # *** trips ***

  def _trips(self) -> list[dict]:
    at, trips = self._trip_index
    if self.mono() - at >= TRIP_INDEX_S:
      trips = timeline.trip_files(self.log_root or "", limit=10_000)
      self._trip_index = (self.mono(), trips)
    return trips

  def trips_list(self) -> tuple[int, dict]:
    if self.vehicle_state().onroad:
      return 409, {"error": "As viagens só abrem com o carro desligado."}
    if not self.log_root:
      return 404, {"error": "Sem pasta de logs."}
    with self._replay_lock:
      trips = self._trips()[:60]
    return 200, {"trips": [{"id": t["id"], "mtime": int(t["mtime"]), "minutes": len(t["files"]),
                            "segments": sorted(t["files"])} for t in trips]}

  def _with_reasons(self, events: list[dict]) -> list[dict]:
    with self.lock:
      marks = [m for m in self._marks() if m.get("mono_ns") and m.get("label")]
    out = []
    for e in events:
      e = dict(e)
      mono = e.pop("mono_ns", None)
      if mono is not None:
        for m in marks:
          if abs(m["mono_ns"] - mono) < 2e9:
            e["text"] = m["label"] + (f": {m['note']}" if m.get("note") else "")
      out.append(e)
    return out

  def trip_data(self, route: str, segment: str) -> tuple[int, dict]:
    if self.vehicle_state().onroad:
      return 409, {"error": "As viagens só abrem com o carro desligado."}
    if not self.log_root:
      return 404, {"error": "Sem pasta de logs."}
    with self._replay_lock:
      trip = next((t for t in self._trips() if t["id"] == route), None)  # only ids from the list: no paths from outside
      path = trip["files"].get(int(segment)) if trip and segment.isdigit() else None
      if path is None:
        return 404, {"error": "Viagem não encontrada."}
      key = f"{route}--{segment}"
      if key not in self._trip_cache:
        try:
          self._trip_cache[key] = timeline.trip_segment(timeline.read_rlog(path))
        except Exception as e:
          return 500, {"error": f"Não foi possível ler a viagem: {type(e).__name__}"}
        while len(self._trip_cache) > TRIP_CACHE:
          self._trip_cache.popitem(last=False)
      self._trip_cache.move_to_end(key)
      data = self._trip_cache[key]
    return 200, {**data, "events": self._with_reasons(data["events"])}

  # *** speed cameras ***

  def radars_state(self) -> dict:
    updated = self.radar_path is not None and self.radar_path.is_file()
    path = self.radar_path if updated else radars.BUNDLED
    with open(path) as f:
      count = sum(1 for line in f if not line.startswith("#"))
    allowed, reason = updates.can_update(self.vehicle_state())
    return {"count": count, "date": int(path.stat().st_mtime) if updated else None, "allowed": allowed, "reason": reason, **self._radar_job}

  def _gps(self) -> tuple[float, float, float, float] | None:
    with self._vs_lock:
      try:
        return self._gps_fix()
      except Exception:
        return None

  def _edits(self) -> list[dict]:
    return self.store.get(radars.EDITS_PARAM) or []

  def _rows(self):
    """The list with the edits, built again only when one of them changes."""
    edits = self._edits()
    path = self.radar_path if self.radar_path is not None and self.radar_path.is_file() else radars.BUNDLED
    stamp = (str(path), path.stat().st_mtime, str(edits), int(self.clock() // 60))  # a minute, for a blitz to expire
    if self._radar_rows[0] != stamp:
      self._radar_rows = (stamp, radars.merge(radars.load(path), edits, self.clock()))
    return self._radar_rows[1], edits

  def radars_near(self) -> dict:
    fix = self._gps()
    with self.lock:
      rows, edits = self._rows()
      mine = [{"key": e["key"], "kind": e["kind"], "limit": e.get("limit"), "t": e.get("t"), "removed": bool(e.get("removed"))}
              for e in reversed(radars.active(edits, self.clock()))][:40]
    if fix is None:
      return {"gps": False, "near": [], "mine": mine}
    return {"gps": True, "near": radars.nearby(rows, edits, fix[0], fix[1], fix[2]), "mine": mine}

  def radars_pin(self) -> tuple[int, dict]:
    """Holds where the car is at the tap: at 60 km/h, ten seconds later it is 170 m ahead of the point."""
    fix = self._gps()
    if fix is None:
      return 409, {"error": "Sem GPS agora: não dá para saber onde é."}
    with self.lock:
      now = self.clock()
      self._radar_pins = {k: v for k, v in self._radar_pins.items() if now - v[0] < radars.PIN_SECONDS}
      pin = secrets.token_urlsafe(8)
      self._radar_pins[pin] = (now, fix)
    return 200, {"pin": pin}

  def radars_edit(self, who: dict, body: dict) -> tuple[int, dict]:
    fix = self._gps()
    point_key = str(body.get("key", ""))
    limit = body.get("limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 20 <= limit <= 130):
      return 400, {"error": "Limite inválido."}
    with self.lock:
      rows, edits = self._rows()
      hit = [i for i in range(len(rows)) if radars.key(rows[i, 0], rows[i, 1]) == point_key]
      if not hit:
        return 404, {"error": "Ponto não encontrado."}
      row = rows[hit[0]]
      side = body.get("side")
      if side in ("mesmo", "contrario") and fix is None:
        return 409, {"error": "Sem GPS para saber o sentido."}
      if side in ("mesmo", "contrario") and fix[3] < radars.DIRECTION_MIN_SPEED:
        return 409, {"error": "Com o carro parado o GPS não sabe o sentido. Marque andando."}
      direction = None if math.isnan(row[3]) else float(row[3])
      if side == "mesmo":
        direction = fix[2]
      elif side == "contrario":
        direction = (fix[2] + 180.) % 360.
      elif side == "?":
        direction = None
      old = next((e for e in edits if e["key"] == point_key), {})
      entry = {**old, "key": point_key, "lat": float(row[0]), "lon": float(row[1]), "kind": old.get("kind", "edit"), "limit": limit,
               "direction": direction, "confirmed": bool(body.get("confirmed")), "removed": bool(body.get("removed")), "who": who.get("name", ""),
               "t": int(self.clock())}
      self.store.put(radars.EDITS_PARAM, radars.apply_edit(edits, entry, self.clock()))
      self._audit({"event": "radar", "who": who.get("name", ""), "key": "removed" if entry["removed"] else "edit", "new": limit})
    return 200, {"ok": True, "message": "Vale na próxima partida do carro."}

  def radars_add(self, who: dict, body: dict) -> tuple[int, dict]:
    kind = str(body.get("kind", ""))
    limit = body.get("limit")
    if kind not in radars.KINDS:
      return 400, {"error": "Tipo desconhecido."}
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 20 <= limit <= 130):
      return 400, {"error": "Limite inválido."}
    with self.lock:
      now = self.clock()
      held = self._radar_pins.pop(str(body.get("pin", "")), None)  # the phone never sends a position
      if held is None or now - held[0] >= radars.PIN_SECONDS:
        return 409, {"error": "Esse toque expirou. Toque de novo ao passar pelo ponto."}
      lat, lon, bearing, speed = held[1]
      direction = bearing if speed >= radars.DIRECTION_MIN_SPEED else None
      rows, edits = self._rows()
      if radars.taken(rows, lat, lon, direction):
        return 409, {"error": "Já tem um ponto aqui: confira na lista."}
      entry = {"key": radars.key(lat, lon), "lat": lat, "lon": lon, "kind": kind, "limit": limit, "direction": direction, "who": who.get("name", ""),
               "t": int(now), "expires": now + radars.BLITZ_HOURS * 3600 if kind == "blitz" else None}
      self.store.put(radars.EDITS_PARAM, radars.apply_edit(edits, entry, now))
      self._audit({"event": "radar", "who": who.get("name", ""), "key": "add", "new": kind})
    return 200, {"ok": True, "message": "Salvo. Vale na próxima partida do carro."}

  def radars_delete(self, who: dict, point_key: str) -> tuple[int, dict]:
    with self.lock:
      edits = self._edits()
      kept = [e for e in edits if e.get("key") != point_key]
      if len(kept) == len(edits):
        return 404, {"error": "Ponto não encontrado."}
      self.store.put(radars.EDITS_PARAM, kept)
      self._audit({"event": "radar", "who": who.get("name", ""), "key": "delete", "new": ""})
    return 200, {"ok": True}

  def radars_update(self, who: dict) -> tuple[int, dict]:
    allowed, reason = updates.can_update(self.vehicle_state())
    if not allowed:
      return 409, {"error": reason}
    if self.radar_path is None:
      return 404, {"error": "Sem lugar para guardar a lista."}
    with self.lock:
      if self._radar_job["busy"]:
        return 409, {"error": "A lista já está sendo baixada."}
      self._radar_job = {"busy": True, "error": ""}
      self._audit({"event": "update", "who": who.get("name", ""), "key": "radars", "new": ""})

    def run():
      try:
        radars.download(self.radar_path)
        self._radar_job = {"busy": False, "error": ""}
      except Exception as e:
        self._radar_job = {"busy": False, "error": f"Não foi possível baixar: {type(e).__name__}"}

    threading.Thread(target=run, daemon=True).start()
    return 200, {"ok": True}

  # *** updates ***

  def update_state(self) -> dict:
    allowed, reason = updates.can_update(self.vehicle_state())
    with self.lock:
      return {**updates.state(self.store), "allowed": allowed, "reason": reason}

  def update_action(self, who: dict, action: str, branch: str = "") -> tuple[int, dict]:
    allowed, reason = updates.can_update(self.vehicle_state())
    with self.lock:
      if not allowed:
        self._audit({"event": "denied", "who": who.get("name", ""), "key": "update", "new": action, "reason": reason})
        return 409, {"error": reason}
      if action == "branch":
        if branch not in updates.branches(self.store.get("UpdaterAvailableBranches")):
          return 400, {"error": "Versão desconhecida."}
        self.store.put("UpdaterTargetBranch", branch)
        self.signal_updater(updates.SIGNALS["check"])
      elif action in updates.SIGNALS:
        self.signal_updater(updates.SIGNALS[action])
      elif action == "install":
        if not self.store.get_bool("UpdateAvailable"):
          return 409, {"error": "Nenhuma versão pronta para instalar."}
        self.store.put_bool("DoReboot", True)
      elif action == "reboot":
        self.store.put_bool("DoReboot", True)
      else:
        return 400, {"error": "Ação desconhecida."}
      self._audit({"event": "update", "who": who.get("name", ""), "key": action, "new": branch})
      return 200, {"ok": True}

  def logout(self, who: dict) -> tuple[int, dict]:
    with self.lock:
      self.store.put(phone.TOKENS_PARAM, phone.revoke_token(self._tokens(), who.get("id", "")))
      self._audit({"event": "logout", "who": who.get("name", "")})
    return 200, {"ok": True}


def make_handler(backend: PanelBackend):
  class Handler(BaseHTTPRequestHandler):
    server_version = "hexaweb"
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002 - keep the request log out of stderr
      pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
      # a replay is ~400 KB of JSON per minute and ~50 KB gzipped
      gz = len(body) >= GZIP_MIN_BYTES and "gzip" in self.headers.get("Accept-Encoding", "")
      if gz:
        body = gzip.compress(body, compresslevel=5)
      self.send_response(status)
      self.send_header("Content-Type", content_type)
      self.send_header("Content-Length", str(len(body)))
      self.send_header("Vary", "Accept-Encoding")
      if gz:
        self.send_header("Content-Encoding", "gzip")
      for k, v in SECURITY_HEADERS.items():
        self.send_header(k, v)
      self.end_headers()
      self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
      self._send(status, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode(), "application/json; charset=utf-8")

    def _allowed(self) -> bool:
      if not phone.is_local_address(self.client_address[0]):
        self._json(403, {"error": "Só na rede local."})
        return False
      return True

    def _who(self) -> dict | None:
      auth = self.headers.get("Authorization", "")
      token = auth[7:] if auth.startswith("Bearer ") else None
      who = backend.authenticate(token)
      if who is None:
        self._json(401, {"error": "Celular não pareado."})
      return who

    def _body(self) -> dict | None:
      try:
        length = int(self.headers.get("Content-Length", "0"))
      except ValueError:
        length = -1
      if not 0 <= length <= MAX_BODY:
        self._json(413, {"error": "Pedido grande demais."})
        return None
      try:
        body = json.loads(self.rfile.read(length) or b"{}")
      except (json.JSONDecodeError, UnicodeDecodeError):
        body = None
      if not isinstance(body, dict):
        self._json(400, {"error": "JSON inválido."})
        return None
      return body

    def do_GET(self):
      if not self._allowed():
        return
      path, _, query = self.path.partition("?")
      if path in STATIC_FILES:
        fn, ctype = STATIC_FILES[path]
        self._send(200, (STATIC_DIR / fn).read_bytes(), ctype)
      elif path == "/api/state":
        who = self._who()
        if who is not None:
          self._json(200, backend.state(who))
      elif path == "/api/marks":
        who = self._who()
        if who is not None:
          self._json(200, backend.marks_state())
      elif path == "/api/marks/export":
        who = self._who()
        if who is not None:
          self._json(200, backend.export_marks())
      elif path == "/api/replay/list":
        if self._who() is not None:
          self._json(*backend.replay_list())
      elif path == "/api/replay/data":
        if self._who() is not None:
          self._json(*backend.replay_data(parse_qs(query).get("id", [""])[0]))
      elif path == "/api/trips":
        if self._who() is not None:
          self._json(*backend.trips_list())
      elif path == "/api/trip/segment":
        if self._who() is not None:
          q = parse_qs(query)
          self._json(*backend.trip_data(q.get("route", [""])[0], q.get("n", [""])[0]))
      elif path == "/api/update":
        if self._who() is not None:
          self._json(200, backend.update_state())
      elif path == "/api/radars":
        if self._who() is not None:
          self._json(200, backend.radars_state())
      elif path == "/api/radars/near":
        if self._who() is not None:
          self._json(200, backend.radars_near())
      elif path == "/api/live":
        if self._who() is not None:
          self._json(200, backend.live())
      else:
        self._json(404, {"error": "Não encontrado."})

    def do_POST(self):
      if not self._allowed():
        return
      path = self.path.split("?", 1)[0]
      if path == "/api/pair":
        body = self._body()
        if body is not None:
          self._json(*backend.pair(str(body.get("pin", "")), str(body.get("name", ""))))
        return
      routes = {
        "/api/option": lambda who, b: backend.set_option(who, str(b.get("key", "")), b.get("value")),
        "/api/mark": lambda who, b: backend.mark(who, str(b.get("reason", "")), b.get("note", ""), b.get("delay_s", 0.)),
        "/api/mark/classify": lambda who, b: backend.classify(who, str(b.get("id", "")), str(b.get("reason", "")), b.get("note", ""),
                                                             b.get("replace_note") is True),
        "/api/reasons": lambda who, b: backend.add_reason(who, str(b.get("label", ""))),
        "/api/reasons/remove": lambda who, b: backend.remove_reason(who, str(b.get("key", ""))),
        "/api/radars": lambda who, b: backend.radars_update(who),
        "/api/radars/edit": lambda who, b: backend.radars_edit(who, b),
        "/api/radars/pin": lambda who, b: backend.radars_pin(),
        "/api/radars/add": lambda who, b: backend.radars_add(who, b),
        "/api/radars/delete": lambda who, b: backend.radars_delete(who, str(b.get("key", ""))),
        "/api/update": lambda who, b: backend.update_action(who, str(b.get("action", "")), str(b.get("branch", ""))),
      }
      if path not in routes and path != "/api/logout":
        self._json(404, {"error": "Não encontrado."})
        return
      who = self._who()
      if who is None:
        return
      if path == "/api/logout":
        self._json(*backend.logout(who))
        return
      body = self._body()
      if body is not None:
        self._json(*routes[path](who, body))

  return Handler


def make_server(backend: PanelBackend, host: str = "0.0.0.0", port: int = phone.PORT) -> ThreadingHTTPServer:
  server = ThreadingHTTPServer((host, port), make_handler(backend))
  server.daemon_threads = True
  return server


def serve(backend: PanelBackend, retry: float = SERVE_RETRY) -> None:
  # selfdrived takes a dead process as a system fault and disengages: the panel must never go down with the car
  from openpilot.common.swaglog import cloudlog
  while True:
    try:
      server = make_server(backend)
      cloudlog.info(f"hexaweb: serving on port {phone.PORT}")
      server.serve_forever()
    except Exception:
      cloudlog.exception("hexaweb: server failed, trying again")
      time.sleep(retry)


def main():
  from openpilot.cereal import messaging
  from openpilot.common.params import Params
  from openpilot.common.swaglog import cloudlog

  params = Params()
  sm = messaging.SubMaster(["deviceState", "carState", "selfdriveState", "carControl", "carOutput", "radarState", "gpsLocationExternal"])

  def vehicle_state() -> VehicleState:
    sm.update(0)
    if sm.recv_frame["deviceState"] > 0:
      onroad = sm["deviceState"].started
    else:
      onroad = not params.get_bool("IsOffroad")
    if not onroad:
      return VehicleState(onroad=False)
    if not (sm.alive["carState"] and sm.alive["selfdriveState"]):
      return VehicleState(onroad=True)  # no fresh data: read only
    cs = sm["carState"]
    return VehicleState(onroad=True, parked=cs.gearShifter == "park", standstill=cs.standstill,
                        engaged=sm["selfdriveState"].enabled)

  def live_state() -> dict:
    sm.update(0)
    now = time.monotonic()
    return hexa_live.snapshot(sm["carState"], sm["selfdriveState"], sm["carControl"], sm["carOutput"], sm["radarState"].leadOne,
                              sm["gpsLocationExternal"], now - sm.recv_time["gpsLocationExternal"], sm["deviceState"],
                              now - sm.recv_time["carState"] < 2.)

  def gps_fix() -> tuple[float, float, float, float] | None:
    sm.update(0)
    g = sm["gpsLocationExternal"]
    if not g.hasFix or g.horizontalAccuracy > radars.MIN_FIX_ACCURACY or time.monotonic() - sm.recv_time["gpsLocationExternal"] > hexa_live.GPS_MAX_AGE_S:
      return None
    return g.latitude, g.longitude, g.bearingDeg, g.speed

  from openpilot.common.hardware.hw import Paths
  backend = PanelBackend(params, vehicle_state, log_event=lambda fields: cloudlog.event("hexa_mark", **fields),
                         log_root=Paths.log_root(), live_state=live_state, radar_path=radars.updated_path(), gps_fix=gps_fix)

  def watch_bookmarks():
    # every bookmark in the log becomes a mark: with the reason from the phone, or "sem motivo" to classify later
    bookmarks = messaging.SubMaster(["userBookmark"])
    while True:
      bookmarks.update(100)
      if bookmarks.updated["userBookmark"]:
        backend.on_bookmark(bookmarks.logMonoTime["userBookmark"])

  threading.Thread(target=watch_bookmarks, daemon=True).start()
  serve(backend)


if __name__ == "__main__":
  main()
