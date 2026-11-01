#!/usr/bin/env python3
"""Hexa phone panel: a small local web page to see the comma and change the fork's options.

Runs only while the "painel no celular" option (HexaWebEnabled) is on. Security rules live in
openpilot/selfdrive/hexa/phone.py; this file only parses HTTP and calls PanelBackend.
"""
import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from openpilot.selfdrive.hexa import marks as hexa_marks
from openpilot.selfdrive.hexa import phone
from openpilot.selfdrive.hexa.options import GROUPS, OPTIONS, get_option
from openpilot.selfdrive.hexa.phone import VehicleState

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 4096
STATIC_FILES = {
  "/": ("index.html", "text/html; charset=utf-8"),
  "/index.html": ("index.html", "text/html; charset=utf-8"),
  "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
}
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
               log_event: Callable[[dict], None] | None = None):
    self.store = store  # Params, or anything with get/put/get_bool/put_bool/remove
    self._vehicle_state = vehicle_state
    self.clock = clock  # wall clock: token, audit and mark dates
    self.mono = mono  # monotonic clock, same as logMonoTime: pairing code expiry, bookmark matching
    self.log_event = log_event or (lambda fields: None)  # writes a 'hexa_mark' event to the log
    self.lock = threading.Lock()
    self._vs_lock = threading.Lock()  # requests run in threads; SubMaster is not thread safe
    self.pin_failures = 0
    self._last_mark: dict[str, float] = {}  # token id -> monotonic time of the last phone mark

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
          if opt.group != gkey:
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
    if opt is None:
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

  def mark(self, who: dict, reason: str, note: str = "") -> tuple[int, dict]:
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
      mark = hexa_marks.new_mark("celular", who=who.get("name", ""), reason=reason, label=label,
                                 note=hexa_marks.clean_note(note), now=self.clock())
      mark["_req_mono_ns"] = int(now_mono * 1e9)
      self.store.put(hexa_marks.MARKS_PARAM, hexa_marks.append(self._marks(), mark))
      self.store.put_bool(hexa_marks.BOOKMARK_REQUEST_PARAM, True)  # the comma raises its own bookmark
    self.log_event(hexa_marks.log_fields(mark))
    return 200, {"ok": True, "mark": hexa_marks.public(mark)}

  def classify(self, who: dict, mark_id: str, reason: str, note: str = "") -> tuple[int, dict]:
    with self.lock:
      label = hexa_marks.reason_label(self._custom_reasons(), reason)
      if label is None:
        return 400, {"error": "Motivo desconhecido."}
      try:
        marks, mark = hexa_marks.set_reason(self._marks(), mark_id, reason, label, note)
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
      self.send_response(status)
      self.send_header("Content-Type", content_type)
      self.send_header("Content-Length", str(len(body)))
      for k, v in SECURITY_HEADERS.items():
        self.send_header(k, v)
      self.end_headers()
      self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
      self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

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
      path = self.path.split("?", 1)[0]
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
        "/api/mark": lambda who, b: backend.mark(who, str(b.get("reason", "")), b.get("note", "")),
        "/api/mark/classify": lambda who, b: backend.classify(who, str(b.get("id", "")), str(b.get("reason", "")), b.get("note", "")),
        "/api/reasons": lambda who, b: backend.add_reason(who, str(b.get("label", ""))),
        "/api/reasons/remove": lambda who, b: backend.remove_reason(who, str(b.get("key", ""))),
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


def main():
  from openpilot.cereal import messaging
  from openpilot.common.params import Params
  from openpilot.common.swaglog import cloudlog

  params = Params()
  sm = messaging.SubMaster(["deviceState", "carState", "selfdriveState"])

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

  backend = PanelBackend(params, vehicle_state, log_event=lambda fields: cloudlog.event("hexa_mark", **fields))

  def watch_bookmarks():
    # every bookmark in the log becomes a mark: with the reason from the phone, or "sem motivo" to classify later
    bookmarks = messaging.SubMaster(["userBookmark"])
    while True:
      bookmarks.update(100)
      if bookmarks.updated["userBookmark"]:
        backend.on_bookmark(bookmarks.logMonoTime["userBookmark"])

  threading.Thread(target=watch_bookmarks, daemon=True).start()
  server = make_server(backend)
  cloudlog.info(f"hexaweb: serving on port {phone.PORT}")
  server.serve_forever()


if __name__ == "__main__":
  main()
