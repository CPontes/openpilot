"""LizPilot: the road's speed limit from the map (CRUISE-03, step 1: show and warn; it never changes the speed).

mapd (github.com/pfeiferj/mapd, MIT) reads OpenStreetMap offline on the comma and publishes mapdOut: the road's name, its
speed limit (m/s, direction of travel included) and the next one ahead. This file:
- decides when mapd runs: the option on, and the binary on the disk matching MAPD_SHA256 (checked when it runs, not only when
  it was installed: a binary of another schema must never run);
- installs mapd: the CTO's own build of mapd 8e5eee6, pinned by URL and sha256 (both empty until the CTO fills them);
- decodes mapd's messages from their raw bytes, so a message of another schema is dropped, never raised (decode());
- warns, from selfdrived, when the car goes above the limit plus the tolerance of Res. CONTRAN 798/2020 (7 km/h measured up to
  100 km/h, 7% above), only on a limit that held for SETTLE_S, once per overshoot;
- gives the phone what to show, and the rules for installing and downloading (unmetered network, free space, car off).

The option (HexaSpeedLimit): 0 desligado, 1 mostrar, 2 avisar. "Ajustar" waits for the + of the ACC (CRUISE-01).
Not driving: it holds for every car.
"""
import hashlib
import json
import os
import stat
import urllib.request
from collections.abc import Callable
from pathlib import Path

OPTION_PARAM = "HexaSpeedLimit"
OFF, SHOW, WARN = 0, 1, 2

MAPD_DIR = Path("/data/lizpilot/mapd")
MAPD_BIN = MAPD_DIR / "mapd"
# The CTO builds mapd 8e5eee6 (the schema in cereal/custom.capnp) on the Mac and publishes it with its hash (owner, 05/11:
# "melhor né, aí temos controle"). Empty: nothing is downloaded and nothing runs.
MAPD_VERSION = "8e5eee6"
MAPD_URL = "https://github.com/CPontes/openpilot/releases/download/mapd-8e5eee6/mapd"  # CTO build of pfeiferj/mapd 8e5eee6 (BUILD-mapd.md)
MAPD_SHA256 = "b8b6c8a2180df22833201023e725da990cbc4ec689fdad870fe9276ea9a126fa"  # reproducible: two clean builds, same hash
MAX_BINARY = 64 * 1024 * 1024
INSTALL_FREE_BYTES = 200 * 1024 * 1024

# Areas the phone offers (mapd_download_menu.json at the repository root, which mapd reads from /data/openpilot), and the free
# space each asks for before it starts: [hipótese] the sizes of the archives were not measured
AREAS = (("br_state.RJ", "Rio de Janeiro (estado)", 2 * 1024 ** 3), ("nation.BR", "Brasil inteiro", 12 * 1024 ** 3))

KPH = 3.6
TOLERANCE_KPH = 7.  # Res. CONTRAN 798/2020: 7 km/h on a measured speed up to 100 km/h
TOLERANCE_PCT = 0.07  # 7% of the measured speed above it
WARN_AFTER_S = 2.  # above the limit this long before it warns: a passing overshoot is no warning
SETTLE_S = 3.  # a limit counts only after it held this long: a 50 ms drop to 0, or 60/61 flapping, is no new limit
REARM_UNDER_S = 5.  # under the limit this long arms the warning again
MIN_LIMIT_KPH = 20.
STALE_S = 1.5  # no mapdOut for this long: no limit


def tolerance_kph(v_kph: float) -> float:
  return TOLERANCE_KPH if v_kph <= 100 else v_kph * TOLERANCE_PCT


def option(params) -> int:
  try:
    v = params.get(OPTION_PARAM)
    v = int(v) if v is not None else OFF
  except (TypeError, ValueError):
    return OFF
  return v if v in (OFF, SHOW, WARN) else OFF


# *** the binary: checked when it runs ***

_hash_cache: dict[tuple, str] = {}


def file_sha256(path: Path) -> str | None:
  """The sha256 of a file, cached by its size and mtime (process_config asks every manager loop). None if unreadable."""
  try:
    st = path.stat()
  except OSError:
    return None
  key = (str(path), st.st_size, st.st_mtime_ns)
  if key not in _hash_cache:
    h = hashlib.sha256()
    try:
      with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
          h.update(chunk)
    except OSError:
      return None
    _hash_cache.clear()
    _hash_cache[key] = h.hexdigest()
  return _hash_cache[key]


def binary_ok(path: Path = MAPD_BIN, sha256: str | None = None) -> bool:
  """The binary is there, executable, and exactly the pinned one. No pin: never."""
  sha256 = MAPD_SHA256 if sha256 is None else sha256
  if len(sha256) != 64:
    return False
  try:
    if not (path.is_file() and os.access(path, os.X_OK)):
      return False
  except OSError:
    return False
  return file_sha256(path) == sha256.lower()


installed = binary_ok


def should_run(started: bool, params, CP) -> bool:
  """process_config: mapd runs, car on or off (it downloads maps parked), while the option is on and the pinned binary is there."""
  return option(params) != OFF and binary_ok()


# *** mapd's messages, from their raw bytes ***

def decode(raw: bytes | None, which: str) -> dict | None:
  """The fields LizPilot reads from a mapdOut or mapdExtendedOut, or None for anything else: another union, another schema,
  bytes that don't parse. Never raises: a mapd of another version must never take selfdrived or the phone panel down."""
  if not raw:
    return None
  try:
    from openpilot.cereal import log
    with log.Event.from_bytes(raw) as ev:
      if ev.which() != which:
        return None
      m = getattr(ev, which)
      if which == "mapdOut":
        return {"speedLimit": float(m.speedLimit), "nextSpeedLimit": float(m.nextSpeedLimit),
                "nextSpeedLimitDistance": float(m.nextSpeedLimitDistance), "tileLoaded": bool(m.tileLoaded), "roadName": str(m.roadName)[:60]}
      if which == "mapdExtendedOut":
        p = m.downloadProgress
        return {"active": bool(p.active), "done": int(p.downloadedFiles), "total": int(p.totalFiles)}
  except Exception:
    return None
  return None


class SpeedLimitWatch:
  """Warns once when the car stays above the limit plus the tolerance. A limit counts only after it held SETTLE_S (a drop to 0
  for a frame, or a limit flapping 60/61, is no news). The warning arms again only when the limit goes down and holds there, or
  after the car stayed under the limit REARM_UNDER_S."""

  def __init__(self, dt: float):
    self.dt = dt
    self.limit_kph = 0.  # the settled limit
    self.candidate, self.candidate_s = 0., 0.
    self.armed = True
    self.over_s = 0.
    self.under_s = 0.

  def update(self, v_cluster_ms: float, limit_ms: float, fresh: bool) -> bool:
    raw = float(round(limit_ms * KPH)) if fresh and limit_ms > 0 else 0.
    if raw != self.candidate:
      self.candidate, self.candidate_s = raw, 0.
    else:
      self.candidate_s += self.dt
    if self.candidate_s >= SETTLE_S and self.candidate != self.limit_kph:
      if 0 < self.candidate < self.limit_kph:  # a lower limit, held: news
        self.armed = True
      self.limit_kph, self.over_s = self.candidate, 0.
    limit = self.limit_kph
    if limit < MIN_LIMIT_KPH:
      self.over_s = 0.
      return False
    v = v_cluster_ms * KPH
    if v <= limit:
      self.under_s += self.dt
      self.over_s = 0.
      if self.under_s >= REARM_UNDER_S:
        self.armed = True
      return False
    self.under_s = 0.
    if v - tolerance_kph(v) > limit:
      self.over_s += self.dt
    else:
      self.over_s = 0.
    if self.armed and self.over_s >= WARN_AFTER_S:
      self.armed = False
      return True
    return False


last_limit_kph = 0  # what the alert says (events.speed_limit_over_alert)


# *** what the phone shows ***

def view(out: dict | None, age_s: float, option_value: int, is_installed: bool, progress: dict | None = None) -> dict:
  """out: decode(..., "mapdOut"); progress: decode(..., "mapdExtendedOut")."""
  res: dict = {"option": option_value, "installed": is_installed, "fresh": False, "pinned": len(MAPD_SHA256) == 64}
  if out is not None and age_s < STALE_S:
    lim, nxt = out["speedLimit"], out["nextSpeedLimit"]
    res.update({
      "fresh": True,
      "tile": out["tileLoaded"],
      "limit": round(lim * KPH) if lim > 0 else None,
      "next": round(nxt * KPH) if nxt > 0 else None,
      "next_m": round(out["nextSpeedLimitDistance"]) if nxt > 0 else None,
      "road": out["roadName"],
    })
  if progress is not None:
    res["download"] = progress
  return res


# *** installing mapd and its maps: car off, unmetered network, free space ***

class InstallError(Exception):
  """What the phone shows (Portuguese, translated there)."""


def check_conditions(offroad: bool, network_ok: bool, free_bytes: int, need_bytes: int) -> None:
  if not offroad:
    raise InstallError("Só com o carro desligado.")
  if not network_ok:
    raise InstallError("Só num Wi-Fi: o comma não baixa pelos dados móveis.")
  if free_bytes < need_bytes:
    raise InstallError("Falta espaço no comma para o mapa desta região.")


def install(url: str = MAPD_URL, sha256: str = MAPD_SHA256, dest: Path = MAPD_BIN,
            fetch: Callable[[str], bytes] | None = None) -> str:
  """Downloads the pinned mapd, checks its sha256, writes it to the disk for good (fsync), and only then makes it executable."""
  if not url or len(sha256) != 64 or not url.startswith("https://"):
    raise InstallError("Esta versão ainda não traz o mapd: falta a revisão do CTO.")
  if fetch is None:
    def fetch(u: str) -> bytes:
      req = urllib.request.Request(u, headers={"User-Agent": "LizPilot/1"})
      with urllib.request.urlopen(req, timeout=120) as r:
        return r.read(MAX_BINARY + 1)
  try:
    data = fetch(url)
  except Exception as e:
    raise InstallError("Sem internet no comma para baixar o mapd.") from e
  got = hashlib.sha256(data).hexdigest()
  if len(data) > MAX_BINARY or got != sha256.lower():
    raise InstallError("O arquivo baixado não é o mapd esperado.")
  dest.parent.mkdir(parents=True, exist_ok=True)
  tmp = dest.with_suffix(".tmp")
  with open(tmp, "wb") as f:
    f.write(data)
    f.flush()
    os.fsync(f.fileno())
  os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
  os.replace(tmp, dest)
  (dest.parent / "VERSION").write_text(json.dumps({"version": MAPD_VERSION, "sha256": got}) + "\n")
  return got


def area_need(area: str) -> int:
  for key, _, need in AREAS:
    if key == area:
      return need
  raise InstallError("Região desconhecida.")


def mapd_in(kind: str, text: str = ""):
  """A mapdIn message: "download" an area from AREAS, or "cancelDownload"."""
  import time
  from openpilot.cereal import log
  msg = log.Event.new_message(logMonoTime=int(time.monotonic() * 1e9), valid=True)  # as messaging.new_message builds it
  m = msg.init("mapdIn")
  m.type = kind
  m.str = text
  return msg


def download_message(area: str):
  area_need(area)
  return mapd_in("download", area)


class DownloadGuard:
  """Stops a map download when the car turns on: mapd downloads with the car off only. Asks once per start."""

  def __init__(self):
    self.sent = False

  def update(self, onroad: bool, downloading: bool) -> bool:
    if not onroad:
      self.sent = False
      return False
    if downloading and not self.sent:
      self.sent = True
      return True
    return False
