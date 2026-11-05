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
import random
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

# The receiver (tools/lizpilot/receiver/README.md): the owner's own Supabase project "lizpilot", in São Paulo. Empty (by the
# environment) and the queue waits and the phone says why. Only https (http only to this machine, for the tests and dev_server.ts).
RECEIVER_URL = os.environ.get("LIZPILOT_SHARE_URL", "https://aykuhzdtlkowzrxhppjc.supabase.co/functions/v1/lizpilot-share")  # in the air since 05/11
# Only a LizPilot build registers (owner, 05/11: "qualquer um usando lizpilot"). The receiver keeps the same word in
# LIZPILOT_BUILD_SECRETS. It is in a public repository: it keeps out what isn't LizPilot, not someone who reads the code.
BUILD_SECRET = "lizpilot-share-2026-11-a7c3e91f"
BRANDS = ("gwm",)  # owner, 05/11: only the Haval; another brand only with its DBC reviewed by hand (DECISIONS.md)

FORMAT = b"LZS1"
FORMAT_VERSION = 1
BEFORE_S = 30.
AFTER_S = 15.
SEGMENT_S = 60.
MAX_BYTES = 8 * 1024 * 1024  # the receiver refuses more
MAX_QUEUE = 20
MAX_KEPT = 100  # items kept in the list, sent ones included
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
TEXT_RUN = re.compile(rb"[A-Za-z0-9 .,:/@+-]{6,}")  # 6 printable characters in a row, 3 letters, 4 different: text
# DBC messages and signals that say where, when, how far or who (whole words of the name, then parts anywhere in it)
PRIVATE_TOKENS = frozenset({
  "GPS", "GNSS", "NAV", "NAVI", "LOC", "LOCATION", "LATT", "LON", "LNG", "ALTITUDE", "PSD",  # where (PSD: VW's map data)
  "ODO", "ODOMETER", "MILEAGE", "TRIP",  # how far
  "TIME", "DATE", "YEAR", "MONTH", "DAY", "WEEKDAY", "DOW", "HOUR", "HOURS", "HR", "MINUTE", "MINUTES", "SECONDS", "CLOCK", "RTC", "UTC",  # when
  "VIN", "SERIAL", "NAME", "PHONE", "CALL", "CONTACT", "TEXT", "ASCII", "CHAR", "STRING", "BT", "BLUETOOTH",  # who
  "ID3", "SONG", "ARTIST", "STATION", "ADDRESS", "FOB", "FOBID", "IMMO", "COUNTRY", "COUNTRYCFG",
  # German (VW, Audi, Skoda, Porsche DBCs): year, month, day, hour, minute, second, time, date, odometer, secret, key
  "JAHR", "MONAT", "TAG", "STUNDE", "SEKUNDE", "UHR", "UHRZEIT", "DATUM", "ZEIT", "KILOMETERSTAND", "KM", "GEHEIMNIS", "SCHLUESSEL",
  "UH",  # "Uhrzeit" in VW's abbreviations (UH_Jahr, UH_Stunde)
})
PRIVATE_PARTS = ("LATITUD", "LONGITUD", "LATT", "LOCAT", "GPS", "GNSS", "ODOMET", "MILEAGE", "VIN_", "PHONE", "BLUETOOTH", "LONGDEG", "LATDEG",
                 "NAVIGAT", "KILOMETERSTAND", "GEHEIM", "UHRZEIT", "FOBID", "SMKFOB", "COUNTRYCFG")
CUT_TIMEOUT_S = 180.
SEND_JITTER_S = 2 * 3600.  # a cut waits a random time up to this before it is sent
TEXT_SHARE = 0.5  # an address with text in half of its frames or more is text

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


def valid_vin(vin: str) -> bool:
  v = (vin or "").strip()
  return len(v) >= MIN_SECRET and set(v) != {"0"}  # "000…0": the car didn't say


def _vin_pieces(vin: str) -> list[bytes]:
  v = vin.strip().upper().encode("ascii", "ignore")
  return [v[i:i + VIN_PIECE] for i in range(len(v) - VIN_PIECE + 1)] if len(v) >= VIN_PIECE else []


def _frames(events: Iterable):
  for ev in events:
    if ev.which() in CAN_SERVICES:
      for c in getattr(ev, ev.which()):
        yield (c.src, c.address), bytes(c.dat)


def vin_addresses(events: Iterable, vins: Iterable[str]) -> set[tuple[int, int]]:
  """(bus, address) whose frames carry a VIN: some cars broadcast it all the time, a few characters per frame. Looks at
  each frame, and at the frames of one address in a row with the first byte (the frame's number) taken out."""
  pieces = [p for v in vins for p in _vin_pieces(v)]
  if not pieces:
    return set()
  whole: dict[tuple[int, int], bytearray] = {}
  tails: dict[tuple[int, int], bytearray] = {}
  for k, d in _frames(events):
    whole.setdefault(k, bytearray()).extend(d)
    tails.setdefault(k, bytearray()).extend(d[1:])
  return {k for k in whole if any(p in bytes(whole[k]).upper() or p in bytes(tails[k]).upper() for p in pieces)}


def text_addresses(events: Iterable) -> set[tuple[int, int]]:
  """(bus, address) whose frames are mostly text: a name, a phone number, a radio station from the head unit. Cars send these
  to the cluster; openpilot never needs them. Mostly: a CRC or a speed reads as text now and then ([log] Haval, 4 routes:
  WHEEL_SPEEDS up to 11% of its frames, every other address under 3%)."""
  def is_text(run: bytes) -> bool:  # letters, and not one byte repeated (a fill or a counter that happens to be printable)
    return sum(chr(c).isalpha() for c in run) >= 3 and len(set(run)) >= 4
  frames: dict[tuple[int, int], list[int]] = {}
  for k, d in _frames(events):
    n = frames.setdefault(k, [0, 0])
    n[0] += 1
    n[1] += any(is_text(m.group()) for m in TEXT_RUN.finditer(d))
  return {k for k, (total, text) in frames.items() if text >= 3 and text >= TEXT_SHARE * total}


def private_signal(name: str) -> bool:
  """A DBC message or signal that says where, when, how far or who: GPS, clock, odometer, VIN, names, phone. Whole words of
  the name (CamelCase split too), then parts anywhere in it. "LAT" alone is lateral (LAT_ACCEL), not latitude: only with "LON"."""
  name = re.sub(r"(?i)time_?gap", "GAP", name)  # the ACC's distance setting, not a clock
  up = name.upper()
  words = re.split(r"[^A-Z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).upper())
  tokens = {re.sub(r"\d+$", "", w) for w in words}  # LOCAL_TIME2 is a TIME
  return bool(tokens & PRIVATE_TOKENS) or any(p in up for p in PRIVATE_PARTS)


_ALLOW_CACHE: dict[str, dict | None] = {}


def can_allowlist(model: str | None) -> dict | None:
  """{address: [signals to zero]} for this car: the addresses its DBC files describe. A message whose name is private goes
  whole; one with only some private signals stays, with those signals zeroed (Honda's CRUISE keeps the cruise and loses the
  odometer). None: no DBC known, no CAN leaves. The checksum of a masked frame no longer matches, on purpose."""
  if not model:
    return None
  if model in _ALLOW_CACHE:
    return _ALLOW_CACHE[model]
  out: dict | None = None
  try:
    from opendbc.can.dbc import DBC
    from opendbc.car.values import PLATFORMS
    platform = PLATFORMS.get(model)
    if platform is not None:
      out = {}
      dropped: set[int] = set()
      for name in platform.config.dbc_dict.values():
        for addr, msg in DBC(name).msgs.items():
          if private_signal(msg.name):
            dropped.add(addr)  # the same address in another of the car's DBC files can't bring it back
            continue
          out.setdefault(addr, []).extend(sig for sig_name, sig in msg.sigs.items() if private_signal(sig_name))
      out = {a: sigs for a, sigs in out.items() if a not in dropped}
  except Exception:
    out = None
  _ALLOW_CACHE[model] = out
  return out


def masked(dat: bytes, signals: list) -> bytes:
  """The frame with these DBC signals set to zero."""
  if not signals:
    return dat
  from opendbc.can.packer import set_value
  out = bytearray(dat)
  for sig in signals:
    set_value(out, sig, 0)
  return bytes(out)


def scrubbed(raw: bytes, hide: Iterable[str]) -> list[str]:
  """Which of the identifiers to hide are still in the cut. Must be empty for it to leave."""
  up = raw.upper()
  return [s for s in hide if len(s) >= MIN_SECRET and set(s) != {"0"} and s.upper().encode("utf-8", "ignore") in up]


def _copy_can(ev, which: str, keep: Callable[[int, int], bool], mask: dict):
  from openpilot.cereal import log
  kept = [c for c in getattr(ev, which) if keep(c.src, c.address)]
  out = log.Event.new_message(logMonoTime=ev.logMonoTime, valid=ev.valid)
  lst = out.init(which, len(kept))
  for dst, c in zip(lst, kept, strict=True):
    dst.address, dst.dat, dst.src = c.address, masked(bytes(c.dat), mask.get(c.address, [])), c.src
  return out, len(getattr(ev, which)) - len(kept)


def cut(events: Iterable, center_ns: int, vin: str = "", hide: Iterable[str] = (), can_allow: dict | set | None = None,
        before_s: float = BEFORE_S, after_s: float = AFTER_S) -> tuple[bytes, dict]:
  """The events from before_s before center_ns to after_s after it, only SERVICES, scrubbed. Returns (zstd of the events,
  stats for the manifest). Raises ShareError when nothing is left or an identifier is still in it.

  CAN: only the addresses in can_allow (can_allowlist(): what this car's DBC describes, nothing private), never the diagnostic
  ones, nor an address that carries a VIN or text. can_allow None: no CAN at all."""
  import zstandard
  lo, hi = center_ns - int(before_s * 1e9), center_ns + int(after_s * 1e9)
  window = [ev for ev in events if lo <= ev.logMonoTime <= hi]
  kept = [ev for ev in window if ev.which() in SERVICES]
  vins = {v.strip() for v in [vin, *(ev.carParams.carVin for ev in kept if ev.which() == "carParams")] if valid_vin(v)}
  allow = can_allow if isinstance(can_allow, dict) else dict.fromkeys(can_allow or (), [])
  candidates = [ev for ev in kept if ev.which() in CAN_SERVICES]
  drop = vin_addresses(candidates, vins) | text_addresses(candidates)

  def keep(bus: int, addr: int) -> bool:
    return addr in allow and not _is_diag(addr) and (bus, addr) not in drop

  counts: dict[str, int] = {}
  can_dropped = 0
  can_kept: set[tuple[int, int]] = set()
  out = bytearray()
  for ev in kept:
    which = ev.which()
    if which in CAN_SERVICES:
      b, n = _copy_can(ev, which, keep, allow)
      can_dropped += n
      can_kept |= {(c.src, c.address) for c in getattr(b, which)}
    else:
      b = ev.as_builder()
      if which == "carParams":
        b.carParams.carVin = ""
    out += b.to_bytes()
    counts[which] = counts.get(which, 0) + 1
  if not counts.get("carState"):
    raise ShareError("O log deste momento não tem o carro.")
  raw = bytes(out)
  if scrubbed(raw, [*vins, *hide]):
    raise ShareError("O trecho ainda tem um dado do carro ou do comma. Não foi enviado.")
  payload = zstandard.ZstdCompressor(level=10).compress(raw)
  stats = {
    "services": dict(sorted(counts.items())),
    "left_out": sorted({ev.which() for ev in window if ev.which() not in SERVICES}),
    "can": "dbc" if can_allow else "none",
    "can_addresses": len(can_kept),
    "can_signals_zeroed": sorted({sig.name for addr in {a for _, a in can_kept} for sig in allow.get(addr, [])}),
    "can_frames_dropped": can_dropped,
    "can_addresses_with_vin": sorted(f"{bus}:{hex(addr)}" for bus, addr in vin_addresses(candidates, vins)),
    "can_addresses_with_text": sorted(f"{bus}:{hex(addr)}" for bus, addr in text_addresses(candidates)),
    "vin_checked": bool(vins),
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


def cut_mark(log_root: str, mark: dict, vin: str = "", hide: Iterable[str] = (), model: str | None = None,
             read: Callable[[str], list] | None = None) -> tuple[bytes, dict]:
  """The cut around a mark: the minute it is in, and the minute before or after when the window crosses into it.

  The minute is the first one that closed after the mark (a drive's minutes follow each other; the last one is shorter). With
  the bookmark, the window is on its exact log time; without it (a mark made parked), on the mark's wall time counted back
  from when that minute's log closed."""
  if read is None:
    from openpilot.selfdrive.hexa.timeline import read_rlog as read
  t = mark.get("t")
  if not isinstance(t, int | float) or isinstance(t, bool):
    raise ShareError("Marca sem hora.")
  segs = segments(log_root)
  seg = min((s for s in segs if s["end"] >= t - 2), key=lambda s: s["end"], default=None)
  if seg is None:
    raise ShareError("O log deste momento não está mais no comma.")
  try:
    events = list(read(seg["file"]))
  except Exception as e:
    raise ShareError("Não foi possível ler o log deste momento.") from e
  if not events:  # the file is there, but nothing in it can be read
    raise ShareError("Não foi possível ler o log deste momento.")
  first, last = min(ev.logMonoTime for ev in events), max(ev.logMonoTime for ev in events)
  if t < seg["end"] - (last - first) / 1e9 - 2:
    raise ShareError("O log deste momento não está mais no comma.")  # between two drives, or its minute was deleted
  delay = mark.get("delay_s")
  delay = float(delay) if isinstance(delay, int | float) and not isinstance(delay, bool) and 0 <= delay <= 300 else 0.
  mono = mark.get("mono_ns")
  at = mono - int(delay * 1e9) if isinstance(mono, int) and not isinstance(mono, bool) else None
  if at is not None and first - 90e9 <= at <= last + 90e9:  # the bookmark itself: the exact log time
    center = at
  else:
    center = last - int((seg["end"] - t) * 1e9)
  by_n = {s["n"]: s for s in segs if s["route"] == seg["route"]}
  for n, before in ((seg["n"] - 1, True), (seg["n"] + 1, False)):
    if n not in by_n or (before and center - BEFORE_S * 1e9 >= first) or (not before and center + AFTER_S * 1e9 <= last):
      continue
    try:
      more = list(read(by_n[n]["file"]))
    except Exception:
      continue  # a minute cut short (the comma lost power): the window just ends there
    events = more + events if before else events + more
  return cut(events, center, vin=vin, hide=hide, can_allow=can_allowlist(model))


class CutInterrupted(Exception):
  """The car was turned on while cutting: the cut stops and is done again later."""


class CutTimeout(Exception):
  """The cut took longer than CUT_TIMEOUT_S: counted as a try, like a log that can't be read."""


def cut_in_child(log_root: str, mark: dict, vin: str = "", hide: Iterable[str] = (), model: str | None = None,
                 offroad: Callable[[], bool] = lambda: True, timeout: float = CUT_TIMEOUT_S) -> tuple[bytes, dict]:
  """cut_mark in a separate process at the lowest priority (_child_cut): reading the logs never holds up the phone panel, and it stops
  if the car is turned on. The VIN goes by stdin, never on the command line."""
  import subprocess
  import sys
  import tempfile
  with tempfile.TemporaryDirectory(prefix="lizpilot-cut-") as tmp:
    out_file = Path(tmp) / "cut.zst"
    job = json.dumps({"log_root": log_root, "mark": mark, "vin": vin, "hide": list(hide), "model": model, "out": str(out_file)})
    p = subprocess.Popen([sys.executable, "-m", "openpilot.selfdrive.hexa.share", "cut"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    assert p.stdin is not None
    p.stdin.write(job)
    p.stdin.close()
    p.stdin = None  # sent and closed: communicate() below only reads the answer
    start = time.monotonic()
    while p.poll() is None:
      if not offroad() or time.monotonic() - start > timeout:
        p.kill()
        p.wait()
        raise CutInterrupted() if not offroad() else CutTimeout()
      time.sleep(0.2)
    out, _ = p.communicate(timeout=10)
    answer = json.loads(out or "{}")
    if "error" in answer:
      raise ShareError(str(answer["error"]))
    if p.returncode != 0 or "stats" not in answer:
      raise RuntimeError(f"cut exited {p.returncode}")  # tried again a few times (Sharer._cut)
    return out_file.read_bytes(), answer["stats"]


def _child_cut() -> None:
  import sys
  os.nice(19)  # the lowest priority: the comma's own processes first
  job = json.loads(sys.stdin.read())
  try:
    payload, stats = cut_mark(job["log_root"], job["mark"], vin=job["vin"], hide=job["hide"], model=job["model"])
  except ShareError as e:
    print(json.dumps({"error": str(e)}))
    return
  Path(job["out"]).write_bytes(payload)
  print(json.dumps({"stats": stats}))


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


def manifest(store, mark: dict, stats: dict, payload: bytes, edition: str, with_note: bool = False) -> dict:
  """with_note: the owner chose, in the dialog that showed it, to send what he typed (the note, and a reason of his own)."""
  info, _ = car_info(store)
  typed = str(mark.get("reason") or "").startswith("c-")  # marks.add_reason: a reason he wrote himself
  return {
    "format": FORMAT_VERSION,
    "mark": {"id": mark.get("id"), "source": mark.get("source"),
             "reason": mark.get("reason") if with_note or not typed else "proprio",
             "label": mark.get("label") if with_note or not typed else None,
             "note": (mark.get("note") or "") if with_note else "",
             "delay_s": mark.get("delay_s") if isinstance(mark.get("delay_s"), int | float) and 0 <= mark["delay_s"] <= 60 else None},
    "month": time.strftime("%Y-%m", time.gmtime(float(mark.get("t") or 0))),  # the day and the hour stay on the comma
    "car": info,
    "software": {"edition": edition, "branch": _text_param(store, "GitBranch")[:40], "commit": _text_param(store, "GitCommit")[:12],
                 "version": _text_param(store, "Version")[:20]},
    "window": {"before_s": BEFORE_S, "after_s": AFTER_S},
    "with_note": bool(with_note),
    "cut": stats,
    "sha256": hashlib.sha256(payload).hexdigest(),
    "bytes": len(payload),
  }


# *** the queue ***

def receiver_ok(url: str) -> bool:
  """https, or http to this machine only."""
  from urllib.parse import urlparse
  u = urlparse(url or "")
  return u.scheme == "https" and bool(u.hostname) or (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"))


def _queue(store) -> list[dict]:
  q = store.get(QUEUE_PARAM)
  return [i for i in q if isinstance(i, dict) and i.get("id")] if isinstance(q, list) else []


def public_item(item: dict) -> dict:
  return {k: item.get(k) for k in ("id", "state", "error", "tries", "bytes", "added", "sent_at", "receipt", "with_note") if item.get(k) is not None}


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
  """The queue and the one worker that cuts and sends. Every step is short and never raises: hexaweb runs it in a thread.

  The phone's requests (enqueue, cancel, forget) and the worker run in different threads. They meet only under self.lock, and
  the worker checks again under it right before and right after each request to the receiver: a cancel or a forget that
  arrives while a cut or a send is under way is never lost, and a forget always reaches the receiver once anything may have."""

  def __init__(self, store, log_root: str, edition: str = "h6gt", url: str | None = None,
               post: Callable[[str, bytes, dict], tuple[int, dict]] = http_post, clock: Callable[[], float] = wall_time,
               cutter: Callable[..., tuple[bytes, dict]] | None = None, share_dir: Path | None = None,
               offroad: Callable[[], bool] = lambda: True, send_jitter_s: float = SEND_JITTER_S):
    self.store = store
    self.log_root = log_root
    self.edition = edition
    self.url = RECEIVER_URL if url is None else url
    self.post = post
    self.clock = clock
    self.offroad = offroad  # asked during the cut: the car turned on stops it
    self.send_jitter_s = send_jitter_s
    self.cutter = cutter or (lambda log_root, mark, **kw: cut_in_child(log_root, mark, offroad=self.offroad, **kw))
    self.dir = share_dir or (Path(log_root).parent / SHARE_DIR)
    self.skew = 0.  # the receiver's clock minus ours, learned when it says ours is off (a comma without GPS or NTP yet)
    self.lock = threading.RLock()  # the queue and the identity: the phone's requests and the worker change them from different threads
    self._sending: str | None = None  # the mark whose package is on its way to the receiver right now

  # what the phone asks

  def enabled(self) -> bool:
    return bool(self.store.get_bool(ENABLED_PARAM))

  def state(self) -> dict:
    with self.lock:
      q = _queue(self.store)
      dev = self.store.get(DEVICE_PARAM)
      return {"enabled": self.enabled(), "receiver": receiver_ok(self.url), "items": [public_item(i) for i in q],
              "forgetting": bool(isinstance(dev, dict) and dev.get("forget")),
              "sent": sum(1 for i in q if i.get("state") == "enviado")}

  def allowed_car(self) -> bool:
    from openpilot.selfdrive.hexa import edition
    return edition.car_brand(self.store) in BRANDS

  def enqueue(self, mark: dict, with_note: bool = False) -> tuple[int, dict]:
    with self.lock:
      if not self.enabled():
        return 409, {"error": "Ligue antes \"enviar trechos aos desenvolvedores\" em Ajustes."}
      if not self.allowed_car():
        return 409, {"error": "Por enquanto, os envios são só do Haval."}
      q = _queue(self.store)
      old = next((i for i in q if i["id"] == mark.get("id")), None)
      if old is not None and old.get("state") != "erro":
        return 200, {"ok": True, "item": public_item(old)}
      q = [i for i in q if i["id"] != mark.get("id")]
      if sum(1 for i in q if i.get("state") in ("fila", "pronto")) >= MAX_QUEUE:
        return 429, {"error": "Muitos trechos esperando. Espere enviar os que estão na fila."}
      item = {"id": str(mark["id"]), "state": "fila", "tries": 0, "next_at": 0., "added": int(self.clock()), "with_note": bool(with_note)}
      q = q + [item]
      for gone in q[:-MAX_KEPT]:  # the oldest go, and their cuts with them
        self._drop_file(gone["id"])
      self.store.put(QUEUE_PARAM, q[-MAX_KEPT:])
      return 200, {"ok": True, "item": public_item(item)}

  def cancel(self, mark_id: str) -> tuple[int, dict]:
    with self.lock:
      q = _queue(self.store)
      item = next((i for i in q if i["id"] == mark_id), None)
      if item is None:
        return 404, {"error": "Este trecho não está na fila."}
      if item.get("state") == "enviado" or self._sending == mark_id:
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
      if isinstance(dev, dict) and (dev.get("used") or dev.get("registered") or self._sending):
        self.store.put(DEVICE_PARAM, dict(dev, forget=True))  # the receiver may have something: ask it
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

  def _item(self, mark_id: str) -> dict | None:
    return next((i for i in _queue(self.store) if i["id"] == mark_id), None)

  def _update(self, mark_id: str, **fields) -> None:
    with self.lock:
      q = _queue(self.store)
      self.store.put(QUEUE_PARAM, [dict(i, **fields) if i["id"] == mark_id else i for i in q])

  def _same_device(self, dev: dict) -> bool:
    now = self.store.get(DEVICE_PARAM)
    return isinstance(now, dict) and now.get("id") == dev["id"] and not now.get("forget")

  def _mark_device(self, dev: dict, **fields) -> None:
    """Change this identity only if it is still the current one and no forget is pending."""
    with self.lock:
      if self._same_device(dev):
        self.store.put(DEVICE_PARAM, dict(self.store.get(DEVICE_PARAM), **fields))

  def _signed_post(self, dev: dict, path: str, body: bytes) -> tuple[int, dict]:
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

  def _register(self, dev: dict) -> bool:
    if dev.get("registered"):
      return True
    body = json.dumps({"device": dev["id"], "key": dev["key"], "build": BUILD_SECRET}).encode()
    status, _ = self._signed_post(dev, "/v1/register", body)
    if status in (200, 201):
      self._mark_device(dev, registered=True)
      return True
    if status == 409:  # the receiver knows this id with another key, and nothing of ours was ever sent with it
      with self.lock:
        if self._same_device(dev) and not dev.get("used"):
          self.store.remove(DEVICE_PARAM)
    return False

  def _retry(self, item: dict, *, error: str, give_up: int = MAX_TRIES) -> None:
    tries = int(item.get("tries", 0)) + 1
    if tries >= give_up:
      self._update(item["id"], state="erro", tries=tries, error=error)
      self._drop_file(item["id"])
    else:
      self._update(item["id"], tries=tries, error=error, next_at=self.clock() + BACKOFF_S[min(tries, len(BACKOFF_S)) - 1])

  def _due(self, item: dict) -> bool:
    wait = float(item.get("next_at", 0)) - self.clock()
    return wait <= 0 or wait > max(BACKOFF_S)  # a clock that jumped back never holds an item for long

  def step(self, offroad: bool, network_ok: bool, marks: list[dict]) -> str:
    """One thing at a time: forget, send, or cut. Returns what it did (for the tests and the log)."""
    try:
      return self._step(offroad, network_ok, marks)
    except Exception:
      return "falhou"

  def _step(self, offroad: bool, network_ok: bool, marks: list[dict]) -> str:
    online = network_ok and receiver_ok(self.url)
    with self.lock:
      dev = self.store.get(DEVICE_PARAM)
      forgetting = isinstance(dev, dict) and dev.get("forget")
    if forgetting:
      if not online:
        return "esperando rede"
      status, _ = self._signed_post(dev, "/v1/forget", b"")
      if status in (200, 204, 404):
        with self.lock:
          if self.store.get(DEVICE_PARAM) == dev:
            self.store.remove(DEVICE_PARAM)
        return "apagado"
      return "esperando rede"
    if not self.enabled():
      with self.lock:  # turned off: what hasn't left yet goes, here and on the disk (owner, 05/11)
        q = _queue(self.store)
        pending = [i for i in q if i.get("state") in ("fila", "pronto")]
        for i in pending:
          if i["id"] != self._sending:
            self._drop_file(i["id"])
        if pending:
          self.store.put(QUEUE_PARAM, [i for i in q if i not in pending or i["id"] == self._sending])
      return "desligado"
    with self.lock:
      q = _queue(self.store)
      ready = next((i for i in q if i.get("state") == "pronto" and self._due(i)), None)
      todo = next((i for i in q if i.get("state") == "fila" and self._due(i)), None)
    if ready is not None and online and offroad:  # only with the car off (owner, 05/11)
      return self._send(ready)
    if todo is not None and offroad:  # reading the rlogs costs CPU: never while driving
      return self._cut(todo, marks)
    return "esperando rede" if ready is not None else "nada"

  def _cut(self, todo: dict, marks: list[dict]) -> str:
    mark = next((m for m in marks if m.get("id") == todo["id"]), None)
    if mark is None:
      self._update(todo["id"], state="erro", error="A marca foi apagada.")
      return "erro"
    info, vin = car_info(self.store)
    if info["brand"] not in BRANDS:
      self._update(todo["id"], state="erro", error="Por enquanto, os envios são só do Haval.")
      return "erro"
    hide = [_text_param(self.store, k) for k in ("DongleId", "HardwareSerial")]
    try:
      payload, stats = self.cutter(self.log_root, mark, vin=vin, hide=hide, model=info["model"])
      data = pack(manifest(self.store, mark, stats, payload, self.edition, with_note=bool(todo.get("with_note"))), payload)
      if len(data) > MAX_BYTES:
        raise ShareError("O trecho ficou grande demais para enviar.")
    except CutInterrupted:
      return "interrompido"  # the car was turned on: cut again later
    except ShareError as e:
      self._update(todo["id"], state="erro", error=str(e))
      return "erro"
    except Exception:  # a broken log, a full disk: a few more tries, then it stops asking
      self._retry(todo, error="Não foi possível ler o log deste momento.", give_up=3)
      return "tentar depois"
    with self.lock:  # cancelled or forgotten while it was being cut: the cut goes too
      still = self._item(todo["id"])
      if still is None or still.get("state") != "fila":
        return "cancelado"
      self.dir.mkdir(parents=True, exist_ok=True)
      tmp = self._file(todo["id"]).with_suffix(".tmp")
      tmp.write_bytes(data)
      os.replace(tmp, self._file(todo["id"]))
      # sent at a random moment within SEND_JITTER_S: when it reaches the receiver doesn't say when the car was parked
      self._update(todo["id"], state="pronto", bytes=len(data), error=None, tries=0, next_at=self.clock() + random.uniform(0., self.send_jitter_s))
    return "cortado"

  def _send(self, ready: dict) -> str:
    try:
      data = self._file(ready["id"]).read_bytes()
    except OSError:
      self._update(ready["id"], state="fila", error=None)  # the cut is gone: cut it again
      return "recortar"
    with self.lock:  # right before: still asked for
      still = self._item(ready["id"])
      if still is None or still.get("state") != "pronto":
        return "cancelado"
      dev = device(self.store)
      self._sending = ready["id"]  # from here on, a forget asks the receiver
    try:
      if not self._register(dev):
        self._retry(ready, error="O receptor não respondeu.")
        return "tentar depois"
      with self.lock:  # registered: still the same identity, no forget asked meanwhile
        if not self._same_device(dev):
          return "cancelado"
        dev = dict(self.store.get(DEVICE_PARAM), used=True)
        self.store.put(DEVICE_PARAM, dev)
      status, out = self._signed_post(dev, "/v1/share", data)
    finally:
      with self.lock:
        self._sending = None
    if status in (200, 201):
      with self.lock:
        sent = {"state": "enviado", "sent_at": int(self.clock()), "error": None, "receipt": str(out.get("id", ""))[:40]}
        if self._item(ready["id"]) is not None:
          self._update(ready["id"], **sent)
        elif not (self.store.get(DEVICE_PARAM) or {}).get("forget"):  # cancelled while on its way: it went, say so
          self.store.put(QUEUE_PARAM, _queue(self.store) + [{"id": ready["id"], "tries": 0, "added": ready.get("added"), **sent}])
      self._drop_file(ready["id"])
      return "enviado"
    if status == 401:  # the receiver forgot this key (or never had it): register again, then retry
      self._mark_device(dev, registered=False)
      self._retry(ready, error="O receptor não reconheceu este comma.")
      return "tentar depois"
    if status in (400, 413, 415, 422):
      self._update(ready["id"], state="erro", error="O receptor recusou este trecho.")
      self._drop_file(ready["id"])
      return "erro"
    self._retry(ready, error="O receptor não respondeu.")  # 0, 408, 429, 5xx
    return "tentar depois"

if __name__ == "__main__":
  import sys
  if sys.argv[1:] == ["cut"]:
    _child_cut()
