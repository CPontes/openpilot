"""LizPilot sharing: the owner sends the developers the moment of a mark, so the system keeps improving in any car.

Off by default (ENABLED_PARAM, "enviar trechos aos desenvolvedores"). With it on, the owner picks a mark on the phone and taps
Enviar; nothing is ever sent by itself. The comma then, with the car off:
1. cuts the rlog around the mark (BEFORE_S before, AFTER_S after);
2. keeps only the services in SERVICES: what the car and openpilot did. Never the GPS, the cameras, the microphone, the
   device's logs or its identity (initData);
3. blanks the VIN in carParams, drops the diagnostic CAN (where the VIN is asked for) and any CAN address that carries a piece of
   the VIN, and refuses the whole cut if the VIN, the dongle id or the serial are still anywhere in it (scrubbed());
4. packs it with a small manifest (car brand and model, software version, the mark's reason and note) and waits for a network that
   is not metered;
5. sends it to the receiver (tools/lizpilot/receiver), signed with this comma's own share key.

The share key is random and is not the dongle id: the receiver never learns who the comma belongs to. "Apagar meus envios"
asks the receiver to delete everything this key sent, and starts a new key.

The receiver keeps it private; tools/lizpilot/envios.py downloads it and turns it back into an rlog for the usual tools.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path

from openpilot.selfdrive.hexa.phone import wall_time

ENABLED_PARAM = "HexaShareEnabled"
DEVICE_PARAM = "HexaShareDevice"  # {"id", "key", "registered"}: random, never the dongle id. DONT_LOG
QUEUE_PARAM = "HexaShareQueue"  # [item]: what the owner asked to send, and how it went. DONT_LOG
SHARE_DIR = "lizpilot_share"  # next to the log folder: the cuts waiting for a network

# The receiver (tools/lizpilot/receiver/README.md). Empty until it is deployed: then the queue waits and the phone says why.
RECEIVER_URL = os.environ.get("LIZPILOT_SHARE_URL", "")

FORMAT = b"LZS1"
FORMAT_VERSION = 1
BEFORE_S = 30.
AFTER_S = 15.
SEGMENT_S = 60.
MAX_BYTES = 8 * 1024 * 1024  # the receiver refuses more
MAX_QUEUE = 20
MAX_TRIES = 8
BACKOFF_S = (60., 300., 1800., 7200.)
TIME_HEADER, DEVICE_HEADER, SIG_HEADER = "X-LizPilot-Time", "X-LizPilot-Device", "X-LizPilot-Signature"
HTTP_TIMEOUT = 60.

# What the car and openpilot did, and nothing that says where, who or with which comma. A service not listed never leaves.
SERVICES = frozenset({
  "can", "sendcan", "pandaStates",
  "carState", "carControl", "carOutput", "carParams",
  "controlsState", "selfdriveState", "onroadEvents", "longitudinalPlan", "driverAssistance", "radarState",
  "drivingModelData", "lateralTorqueParameters", "vehicleParameters", "lateralDelay",
  "driverMonitoringState", "accelerometer", "userBookmark",
})
CAN_SERVICES = ("can", "sendcan")
# The diagnostic CAN, where openpilot asks the car for its VIN and firmware while fingerprinting (UDS/ISO-TP)
DIAG_RANGES = ((0x700, 0x7FF), (0x18DA0000, 0x18DBFFFF))
VIN_PIECE = 6  # a CAN address whose bytes hold 6 characters in a row of the VIN carries it
MIN_SECRET = 8  # shorter identifiers would match by chance

STATES = ("fila", "pronto", "enviado", "erro")  # queued, cut and waiting for the network, sent, failed for good


class ShareError(Exception):
  """A cut that can't be made or must not leave. The message is the phone's (Portuguese, translated there)."""


# *** identity and signature (the receiver checks the same: tools/lizpilot/receiver/handler.ts) ***

def device(store) -> dict:
  """This comma's share identity, made the first time it is needed."""
  d = store.get(DEVICE_PARAM)
  if isinstance(d, dict) and re.fullmatch(r"[0-9a-f]{32}", str(d.get("id", ""))) and re.fullmatch(r"[0-9a-f]{64}", str(d.get("key", ""))):
    return d
  d = {"id": secrets.token_hex(16), "key": secrets.token_hex(32), "registered": False}
  store.put(DEVICE_PARAM, d)
  return d


def canonical(ts: int, method: str, path: str, body: bytes) -> bytes:
  return f"{ts}\n{method.upper()}\n{path}\n{hashlib.sha256(body).hexdigest()}".encode()


def sign(key_hex: str, ts: int, method: str, path: str, body: bytes) -> str:
  return hmac.new(bytes.fromhex(key_hex), canonical(ts, method, path, body), hashlib.sha256).hexdigest()


def headers(dev: dict, method: str, path: str, body: bytes, now: float) -> dict:
  ts = int(now)
  return {TIME_HEADER: str(ts), DEVICE_HEADER: dev["id"], SIG_HEADER: sign(dev["key"], ts, method, path, body)}


# *** the package: FORMAT, the manifest's length (4 bytes, big endian), the manifest (JSON), the cut (zstd of the rlog's events) ***

def pack(manifest: dict, payload: bytes) -> bytes:
  m = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
  return FORMAT + struct.pack(">I", len(m)) + m + payload


def unpack(data: bytes) -> tuple[dict, bytes]:
  if len(data) < 8 or data[:4] != FORMAT:
    raise ValueError("not-a-share")
  n = struct.unpack(">I", data[4:8])[0]
  if n > len(data) - 8 or n > 64 * 1024:
    raise ValueError("bad-manifest-length")
  manifest = json.loads(data[8:8 + n])
  if not isinstance(manifest, dict):
    raise ValueError("bad-manifest")
  return manifest, data[8 + n:]


# *** the cut ***

def _is_diag(address: int) -> bool:
  return any(lo <= address <= hi for lo, hi in DIAG_RANGES)


def _vin_pieces(vin: str) -> list[bytes]:
  v = vin.strip().upper().encode("ascii", "ignore")
  return [v[i:i + VIN_PIECE] for i in range(len(v) - VIN_PIECE + 1)] if len(v) >= VIN_PIECE else []


def vin_addresses(events: Iterable, vin: str) -> set[tuple[int, int]]:
  """(bus, address) whose frames carry the VIN: some cars broadcast it all the time, a few characters per frame. Looks at
  each frame, and at the frames of one address in a row with the first byte (the frame's number) taken out."""
  pieces = _vin_pieces(vin)
  if not pieces:
    return set()
  whole: dict[tuple[int, int], bytearray] = {}
  tails: dict[tuple[int, int], bytearray] = {}
  for ev in events:
    if ev.which() not in CAN_SERVICES:
      continue
    for c in getattr(ev, ev.which()):
      k = (c.src, c.address)
      d = bytes(c.dat)
      whole.setdefault(k, bytearray()).extend(d)
      tails.setdefault(k, bytearray()).extend(d[1:])
  found = set()
  for k in whole:
    up_whole, up_tails = bytes(whole[k]).upper(), bytes(tails[k]).upper()
    if any(p in up_whole or p in up_tails for p in pieces):
      found.add(k)
  return found


def scrubbed(raw: bytes, hide: Iterable[str]) -> list[str]:
  """Which of the identifiers to hide are still in the cut. Must be empty for it to leave."""
  up = raw.upper()
  return [s for s in hide if len(s) >= MIN_SECRET and s.upper().encode("utf-8", "ignore") in up]


def _copy_can(ev, which: str, drop: set[tuple[int, int]]):
  from openpilot.cereal import log
  kept = [c for c in getattr(ev, which) if not _is_diag(c.address) and (c.src, c.address) not in drop]
  out = log.Event.new_message(logMonoTime=ev.logMonoTime, valid=ev.valid)
  lst = out.init(which, len(kept))
  for dst, c in zip(lst, kept, strict=True):
    dst.address, dst.dat, dst.src = c.address, bytes(c.dat), c.src
  return out, len(getattr(ev, which)) - len(kept)


def cut(events: Iterable, center_ns: int, vin: str = "", hide: Iterable[str] = (),
        before_s: float = BEFORE_S, after_s: float = AFTER_S) -> tuple[bytes, dict]:
  """The events from before_s before center_ns to after_s after it, only SERVICES, scrubbed. Returns (zstd of the events,
  stats for the manifest). Raises ShareError when nothing is left or an identifier is still in it."""
  import zstandard
  lo, hi = center_ns - int(before_s * 1e9), center_ns + int(after_s * 1e9)
  window = [ev for ev in events if lo <= ev.logMonoTime <= hi]
  kept = [ev for ev in window if ev.which() in SERVICES]
  drop = vin_addresses(kept, vin)
  counts: dict[str, int] = {}
  can_dropped = 0
  out = bytearray()
  for ev in kept:
    which = ev.which()
    if which in CAN_SERVICES:
      b, n = _copy_can(ev, which, drop)
      can_dropped += n
    else:
      b = ev.as_builder()
      if which == "carParams":
        b.carParams.carVin = ""
    out += b.to_bytes()
    counts[which] = counts.get(which, 0) + 1
  if not counts.get("carState"):
    raise ShareError("O log deste momento não tem o carro.")
  raw = bytes(out)
  hide = [vin, *hide]
  if scrubbed(raw, hide):
    raise ShareError("O trecho ainda tem um dado do carro ou do comma. Não foi enviado.")
  payload = zstandard.ZstdCompressor(level=10).compress(raw)
  stats = {
    "services": dict(sorted(counts.items())),
    "left_out": sorted({ev.which() for ev in window if ev.which() not in SERVICES}),
    "can_frames_dropped": can_dropped,
    "can_addresses_with_vin": sorted(f"{bus}:{hex(addr)}" for bus, addr in drop),
    "vin_checked": len(vin.strip()) >= MIN_SECRET,
    "seconds": round((max(ev.logMonoTime for ev in kept) - min(ev.logMonoTime for ev in kept)) / 1e9, 1),
  }
  return payload, stats


# *** where a mark is in the logs ***

def segments(log_root: str) -> list[dict]:
  """The rlogs on the comma: [{"route", "n", "file", "end"}], end = when the minute's log closed (wall clock)."""
  root = Path(log_root)
  out = []
  if not root.is_dir():
    return out
  for d in root.iterdir():
    route, sep, seg = d.name.rpartition("--")
    if not sep or not seg.isdigit() or not d.is_dir():
      continue
    for name in ("rlog.zst", "rlog", "rlog.bz2"):
      f = d / name
      if f.is_file():
        try:
          out.append({"route": route, "n": int(seg), "file": str(f), "end": f.stat().st_mtime})
        except OSError:
          pass
        break
  return out


def cut_mark(log_root: str, mark: dict, vin: str = "", hide: Iterable[str] = (),
             read: Callable[[str], list] | None = None) -> tuple[bytes, dict]:
  """The cut around a mark: the minute it is in, and the minute before or after when the window crosses into it."""
  if read is None:
    from openpilot.selfdrive.hexa.timeline import read_rlog as read
  t = mark.get("t")
  if not isinstance(t, int | float):
    raise ShareError("Marca sem hora.")
  segs = segments(log_root)
  seg = next((s for s in sorted(segs, key=lambda s: -s["end"]) if s["end"] - SEGMENT_S - 2 <= t <= s["end"] + 2), None)
  if seg is None:
    raise ShareError("O log deste momento não está mais no comma.")
  by_n = {s["n"]: s for s in segs if s["route"] == seg["route"]}
  events = list(read(seg["file"]))
  if not events:
    raise ShareError("O log deste momento não está mais no comma.")
  first, last = min(ev.logMonoTime for ev in events), max(ev.logMonoTime for ev in events)
  delay = float(mark.get("delay_s") or 0.)
  mono = mark.get("mono_ns")
  if isinstance(mono, int) and first - 90e9 <= mono <= last + 90e9:  # the bookmark itself: the exact log time
    center = mono - int(delay * 1e9)
  else:  # a mark made parked, or one whose bookmark never came: its wall time inside the minute
    center = first + int(max(0., t - (seg["end"] - SEGMENT_S)) * 1e9)
  if center - BEFORE_S * 1e9 < first and seg["n"] - 1 in by_n:
    events = list(read(by_n[seg["n"] - 1]["file"])) + events
  if center + AFTER_S * 1e9 > last and seg["n"] + 1 in by_n:
    events += list(read(by_n[seg["n"] + 1]["file"]))
  return cut(events, center, vin=vin, hide=hide)


def car_info(store) -> tuple[dict, str]:
  """({brand, model}, VIN) from CarParamsPersistent. The VIN is only used to look for it in the cut, and never leaves."""
  raw = store.get("CarParamsPersistent")
  if not isinstance(raw, bytes | bytearray) or not raw:
    return {"brand": None, "model": None}, ""
  try:
    from opendbc.car.structs import car
    with car.CarParams.from_bytes(bytes(raw)) as cp:
      return {"brand": str(cp.brand) or None, "model": str(cp.carFingerprint) or None}, str(cp.carVin or "")
  except Exception:
    return {"brand": None, "model": None}, ""


def _text_param(store, key: str) -> str:
  v = store.get(key)
  return v.decode("utf-8", "ignore") if isinstance(v, bytes) else str(v or "")


def manifest(store, mark: dict, stats: dict, payload: bytes, edition: str) -> dict:
  info, _ = car_info(store)
  return {
    "format": FORMAT_VERSION,
    "mark": {k: mark.get(k) for k in ("id", "reason", "label", "note", "delay_s", "source")},
    "month": time.strftime("%Y-%m", time.gmtime(float(mark.get("t") or 0))),  # the day and the hour stay on the comma
    "car": info,
    "software": {"edition": edition, "branch": _text_param(store, "GitBranch")[:40], "commit": _text_param(store, "GitCommit")[:12],
                 "version": _text_param(store, "Version")[:20]},
    "window": {"before_s": BEFORE_S, "after_s": AFTER_S},
    "cut": stats,
    "sha256": hashlib.sha256(payload).hexdigest(),
    "bytes": len(payload),
  }


# *** the queue ***

def _queue(store) -> list[dict]:
  q = store.get(QUEUE_PARAM)
  return [i for i in q if isinstance(i, dict) and i.get("id")] if isinstance(q, list) else []


def public_item(item: dict) -> dict:
  return {k: item.get(k) for k in ("id", "state", "error", "tries", "bytes", "added", "sent_at") if item.get(k) is not None}


def http_post(url: str, body: bytes, hdrs: dict, timeout: float = HTTP_TIMEOUT) -> tuple[int, dict]:
  req = urllib.request.Request(url, data=body, method="POST", headers={**hdrs, "Content-Type": "application/octet-stream",
                                                                         "User-Agent": "lizpilot-share/1"})
  try:
    with urllib.request.urlopen(req, timeout=timeout) as r:
      status, data = r.status, r.read(64 * 1024)
  except urllib.error.HTTPError as e:
    status, data = e.code, e.read(64 * 1024)
  try:
    out = json.loads(data or b"{}")
  except ValueError:
    out = {}
  return status, out if isinstance(out, dict) else {}


class Sharer:
  """The queue and the one worker that cuts and sends. Every step is short and never raises: hexaweb runs it in a thread."""

  def __init__(self, store, log_root: str, edition: str = "h6gt", url: str | None = None,
               post: Callable[[str, bytes, dict], tuple[int, dict]] = http_post, clock: Callable[[], float] = wall_time,
               cutter: Callable[..., tuple[bytes, dict]] = cut_mark, share_dir: Path | None = None):
    self.store = store
    self.log_root = log_root
    self.edition = edition
    self.url = RECEIVER_URL if url is None else url
    self.post = post
    self.clock = clock
    self.cutter = cutter
    self.dir = share_dir or (Path(log_root).parent / SHARE_DIR)
    self.skew = 0.  # the receiver's clock minus ours, learned when it says ours is off (a comma without GPS or NTP yet)
    self.lock = threading.RLock()  # the queue: the phone's requests and the worker change it from different threads

  # what the phone asks

  def enabled(self) -> bool:
    return bool(self.store.get_bool(ENABLED_PARAM))

  def state(self) -> dict:
    with self.lock:
      q = _queue(self.store)
      dev = self.store.get(DEVICE_PARAM)
      return {"enabled": self.enabled(), "receiver": bool(self.url), "items": [public_item(i) for i in q],
              "forgetting": bool(isinstance(dev, dict) and dev.get("forget")),
              "sent": sum(1 for i in q if i.get("state") == "enviado")}

  def enqueue(self, mark: dict) -> tuple[int, dict]:
    with self.lock:
      if not self.enabled():
        return 409, {"error": "Ligue antes \"enviar trechos aos desenvolvedores\" em Ajustes."}
      q = _queue(self.store)
      old = next((i for i in q if i["id"] == mark.get("id")), None)
      if old is not None and old.get("state") != "erro":
        return 200, {"ok": True, "item": public_item(old)}
      q = [i for i in q if i["id"] != mark.get("id")]
      if sum(1 for i in q if i.get("state") in ("fila", "pronto")) >= MAX_QUEUE:
        return 429, {"error": "Muitos trechos esperando. Espere enviar os que estão na fila."}
      item = {"id": str(mark["id"]), "state": "fila", "tries": 0, "next_at": 0., "added": int(self.clock())}
      q = (q + [item])[-100:]
      self.store.put(QUEUE_PARAM, q)
      return 200, {"ok": True, "item": public_item(item)}

  def cancel(self, mark_id: str) -> tuple[int, dict]:
    with self.lock:
      q = _queue(self.store)
      item = next((i for i in q if i["id"] == mark_id), None)
      if item is None:
        return 404, {"error": "Este trecho não está na fila."}
      if item.get("state") == "enviado":
        return 409, {"error": "Este trecho já foi enviado. Para apagar, use \"apagar meus envios\"."}
      self._drop_file(mark_id)
      self.store.put(QUEUE_PARAM, [i for i in q if i["id"] != mark_id])
      return 200, {"ok": True}

  def forget(self) -> tuple[int, dict]:
    """Delete everything here, and ask the receiver to delete what this key sent (at the next network)."""
    with self.lock:
      for i in _queue(self.store):
        self._drop_file(i["id"])
      self.store.put(QUEUE_PARAM, [])
      dev = self.store.get(DEVICE_PARAM)
      if isinstance(dev, dict) and dev.get("registered"):
        self.store.put(DEVICE_PARAM, dict(dev, forget=True))
      else:
        self.store.remove(DEVICE_PARAM)  # never reached the receiver: nothing there to delete
      return 200, {"ok": True}

  # the worker

  def _file(self, mark_id: str) -> Path:
    return self.dir / f"{re.sub(r'[^0-9a-zA-Z_-]', '', mark_id)}.lzs"

  def _drop_file(self, mark_id: str) -> None:
    try:
      self._file(mark_id).unlink()
    except OSError:
      pass

  def _update(self, mark_id: str, **fields) -> None:
    with self.lock:
      q = _queue(self.store)
      self.store.put(QUEUE_PARAM, [dict(i, **fields) if i["id"] == mark_id else i for i in q])

  def _signed_post(self, path: str, body: bytes) -> tuple[int, dict]:
    dev = device(self.store)
    try:
      status, out = self.post(self.url.rstrip("/") + path, body, headers(dev, "POST", path, body, self.clock() + self.skew))
    except Exception:
      return 0, {}  # no network, DNS, TLS: try again later
    now = out.get("now")
    if status == 401 and out.get("error") == "clock" and isinstance(now, int | float) and not isinstance(now, bool):
      self.skew = float(now) - self.clock()
      try:  # once more, on the receiver's clock
        status, out = self.post(self.url.rstrip("/") + path, body, headers(dev, "POST", path, body, self.clock() + self.skew))
      except Exception:
        return 0, {}
    return status, out

  def _register(self) -> bool:
    dev = device(self.store)
    if dev.get("registered"):
      return True
    body = json.dumps({"device": dev["id"], "key": dev["key"]}).encode()
    status, _ = self._signed_post("/v1/register", body)
    if status in (200, 201):
      self.store.put(DEVICE_PARAM, dict(dev, registered=True))
      return True
    if status == 409:  # the receiver knows this id with another key: start a new identity
      self.store.remove(DEVICE_PARAM)
    return False

  def _retry(self, item: dict, *, error: str) -> None:
    tries = int(item.get("tries", 0)) + 1
    if tries >= MAX_TRIES:
      self._update(item["id"], state="erro", tries=tries, error=error)
      self._drop_file(item["id"])
    else:
      self._update(item["id"], tries=tries, error=error, next_at=self.clock() + BACKOFF_S[min(tries, len(BACKOFF_S)) - 1])

  def step(self, offroad: bool, network_ok: bool, marks: list[dict]) -> str:
    """One thing at a time: forget, cut, or send. Returns what it did (for the tests and the log)."""
    try:
      return self._step(offroad, network_ok, marks)
    except Exception:
      return "falhou"

  def _step(self, offroad: bool, network_ok: bool, marks: list[dict]) -> str:
    dev = self.store.get(DEVICE_PARAM)
    if isinstance(dev, dict) and dev.get("forget"):
      if not (network_ok and self.url):
        return "esperando rede"
      status, _ = self._signed_post("/v1/forget", b"")
      if status in (200, 204, 404):
        self.store.remove(DEVICE_PARAM)
        return "apagado"
      return "esperando rede"
    if not self.enabled():
      return "desligado"
    q = _queue(self.store)
    todo = next((i for i in q if i.get("state") == "fila"), None)
    if todo is not None and offroad:  # reading the rlogs costs CPU: never while driving
      mark = next((m for m in marks if m.get("id") == todo["id"]), None)
      if mark is None:
        self._update(todo["id"], state="erro", error="A marca foi apagada.")
        return "erro"
      _, vin = car_info(self.store)
      hide = [_text_param(self.store, k) for k in ("DongleId", "HardwareSerial")]
      try:
        payload, stats = self.cutter(self.log_root, mark, vin=vin, hide=hide)
        man = manifest(self.store, mark, stats, payload, self.edition)
        data = pack(man, payload)
        if len(data) > MAX_BYTES:
          raise ShareError("O trecho ficou grande demais para enviar.")
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._file(todo["id"]).with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, self._file(todo["id"]))
      except ShareError as e:
        self._update(todo["id"], state="erro", error=str(e))
        return "erro"
      with self.lock:  # cancelled or forgotten while it was being cut: the cut goes too
        still = next((i for i in _queue(self.store) if i["id"] == todo["id"]), None)
        if still is None or still.get("state") != "fila":
          self._drop_file(todo["id"])
          return "cancelado"
        self._update(todo["id"], state="pronto", bytes=len(data), error=None)
      return "cortado"
    ready = next((i for i in q if i.get("state") == "pronto" and float(i.get("next_at", 0)) <= self.clock()), None)
    if ready is None:
      return "nada"
    if not (network_ok and self.url):
      return "esperando rede"
    try:
      data = self._file(ready["id"]).read_bytes()
    except OSError:
      self._update(ready["id"], state="fila", error=None)  # the cut is gone: cut it again
      return "recortar"
    if not self._register():
      self._retry(ready, error="O receptor não respondeu.")
      return "tentar depois"
    status, out = self._signed_post("/v1/share", data)
    if status in (200, 201):
      self._update(ready["id"], state="enviado", sent_at=int(self.clock()), error=None, receipt=str(out.get("id", ""))[:40])
      self._drop_file(ready["id"])
      return "enviado"
    if status == 401:  # the receiver forgot this key (or never had it): register again, then retry
      self.store.put(DEVICE_PARAM, dict(device(self.store), registered=False))
      self._retry(ready, error="O receptor não reconheceu este comma.")
      return "tentar depois"
    if status in (400, 413, 415, 422):
      self._update(ready["id"], state="erro", error="O receptor recusou este trecho.")
      self._drop_file(ready["id"])
      return "erro"
    self._retry(ready, error="O receptor não respondeu.")  # 0, 408, 429, 5xx
    return "tentar depois"
