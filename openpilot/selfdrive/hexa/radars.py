"""Speed cameras and speed bumps ahead: the list on the comma and the warning logic. It only warns: nothing here touches the car.

The list comes from OpenStreetMap (c) OpenStreetMap contributors, ODbL. It stays on the comma, so the warning works with
no internet and the car's position goes nowhere. Most cameras have no direction in the data, so one on the other side of
a two-way road can warn too. A warning never stands in for watching the speed.
"""
import hashlib
import json
import math
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

BUNDLED = Path(__file__).parent / "radars_br.csv"
HEADER = "# speed cameras, (c) OpenStreetMap contributors, ODbL. lat,lon,limit km/h,direction deg\n"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_QUERY = """[out:json][timeout:120];
area["ISO3166-1"="BR"][admin_level=2]->.br;
(node["highway"="speed_camera"](area.br);node["enforcement"="maxspeed"](area.br););
out body;"""
EDITS_PARAM = "HexaRadarEdits"
MAX_EDITS = 500
MATCH_RADIUS = 25.  # m, an edit finds its camera again after the list is updated
RADAR, BLITZ, BUMP, SIGNAL = 0, 1, 2, 3  # SIGNAL: a red-light camera ("radar de sinal"), often with a speed limit too
KINDS = {"radar": RADAR, "blitz": BLITZ, "lombada": BUMP, "sinal": SIGNAL}
KIND_NAMES = {v: k for k, v in KINDS.items()}
KIND_TEXT = {RADAR: "Radar", BLITZ: "Blitz", BUMP: "Lombada", SIGNAL: "Radar de sinal"}
SIDES = ("mesmo", "contrario", "dois")  # as the driver sees it: this way, the other way, both ways
CAMERA_LIMITS = (20, 130)  # km/h
BUMP_SPEEDS = (5, 60)  # km/h, the speed the owner wants over a bump
BLITZ_HOURS = 12.  # a checkpoint moves: it leaves by itself
PIN_SECONDS = 300.  # how long a tap holds the place while the note is filled in
DIRECTION_MIN_SPEED = 2.  # m/s, the GPS bearing wanders below this and freezes when stopped
NEAR_LIST = 10
LIST_REACH = 3000.  # m
NEAR_BEHIND = 300.  # m, a point just passed can still be checked. Farther back it only hides the ones ahead
MAX_DOWNLOAD = 8 * 1024 * 1024
MIN_RADARS = 1000  # fewer than this is a bad answer, not a smaller country
COMPASS = dict(zip(("N", "NE", "E", "SE", "S", "SW", "W", "NW"), range(0, 360, 45), strict=True))

M_PER_DEG = 111320.
STEP_S = 1.  # the GPS is 10 Hz, a look per second is plenty
MIN_SPEED = 5.  # m/s, parked or crawling there's nothing to warn about
MIN_FIX_ACCURACY = 30.  # m
NEAR_RADIUS = 6000.  # m, only this part of the list is looked at
NEAR_REFRESH = 1500.  # m driven before picking that part again
REACH = 1700.  # m, the farthest a warning can come
CONE = 18.  # deg, off the heading
CONE_CLOSE = 35.
CLOSE = 150.  # m
MAX_CROSS = 45.  # m, sideways from the car's line: a parallel street stays out
DIRECTION_TOLERANCE = 60.  # deg
SIGHTINGS = 2  # looks in a row, getting closer
FORGET = 2500.  # m, past this a warned camera can warn again on the way back

SECONDS_AHEAD = 12.  # within the limit: a warning this long before
LIMIT_MARGIN = 50.  # m
REACTION = 3.  # s
DECEL = .5  # m/s^2, the same comfort limit the slowdown will use
MIN_DISTANCE = 150.
MAX_DISTANCE = 1500.


def alert_distance(speed: float, limit: float | None) -> float:
  """How far ahead to warn, in m. Over the limit, far enough to get down to it gently."""
  distance = speed * SECONDS_AHEAD
  if limit is not None and speed * 3.6 > limit:
    target = limit / 3.6
    distance = max(distance, LIMIT_MARGIN + REACTION * speed + (speed ** 2 - target ** 2) / (2 * DECEL))
  return float(np.clip(distance, MIN_DISTANCE, MAX_DISTANCE))


def _number(text) -> float:
  m = re.fullmatch(r"\s*(\d{1,3})(\.\d+)?\s*(km/h)?\s*", str(text))
  return float(m.group(1)) if m else float("nan")


def _direction(text) -> float:
  text = str(text).strip()
  if text.upper() in COMPASS:
    return float(COMPASS[text.upper()])
  value = _number(text)
  return value if value < 360 else float("nan")


def parse_overpass(data: dict) -> np.ndarray:
  rows = set()
  for e in data.get("elements", []):
    if e.get("type") != "node":
      continue
    tags = e.get("tags", {})
    limit = _number(tags.get("maxspeed", ""))
    limit = limit if 20 <= limit <= 130 else float("nan")
    rows.add((round(float(e["lat"]), 6), round(float(e["lon"]), 6), limit, _direction(tags.get("direction", ""))))
  return np.array(sorted(rows, key=str), dtype=float).reshape(-1, 4)


def save(rows: np.ndarray, path) -> None:
  tmp = f"{path}.tmp"
  with open(tmp, "w") as f:
    f.write(HEADER)
    for lat, lon, limit, direction in rows:
      f.write(f"{lat:.6f},{lon:.6f},{limit:.0f},{direction:.0f}\n")
  os.replace(tmp, path)


def load(path) -> np.ndarray:
  return np.loadtxt(path, delimiter=",", comments="#", ndmin=2).reshape(-1, 4)


def updated_path() -> Path:
  from openpilot.common.hardware.hw import Paths
  return Path(Paths.log_root()).parent / "hexa_radars.csv"


def current() -> Path:
  """The list downloaded from the phone when there is one, otherwise the one that came with this version."""
  updated = updated_path()
  return updated if updated.is_file() else BUNDLED


def download(path) -> int:
  """Fetches the list and swaps it in only when the answer looks right. Returns how many cameras it has."""
  body = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode()
  req = urllib.request.Request(OVERPASS_URL, data=body, headers={"User-Agent": "h6gt-comma"})
  with urllib.request.urlopen(req, timeout=150) as r:
    raw = r.read(MAX_DOWNLOAD + 1)
  if len(raw) > MAX_DOWNLOAD:
    raise ValueError("answer too large")
  rows = parse_overpass(json.loads(raw))
  if len(rows) < MIN_RADARS:
    raise ValueError("too few cameras")
  save(rows, path)
  return len(rows)


def key(lat: float, lon: float) -> str:
  """An opaque id: the phone gets distances, never a position."""
  return hashlib.sha256(f"{lat:.5f},{lon:.5f}".encode()).hexdigest()[:12]


def _meters(rows: np.ndarray, lat: float, lon: float) -> tuple[np.ndarray, np.ndarray]:
  k = math.cos(math.radians(lat))
  return (rows[:, 1] - lon) * k * M_PER_DEG, (rows[:, 0] - lat) * M_PER_DEG


def merge(rows: np.ndarray, edits: list[dict] | None, now: float) -> np.ndarray:
  """The list with the owner's corrections and points on top, with a fifth column for the kind.

  Edits stay apart from the list and find their camera by position, so a new list doesn't lose them.
  """
  out = np.c_[rows, np.zeros(len(rows))]
  added = []
  for e in edits or []:
    limit = float("nan") if e.get("limit") is None else float(e["limit"])
    direction = float("nan") if e.get("direction") is None else float(e["direction"])
    if e.get("kind") in KINDS:
      if not e.get("removed") and (e.get("expires") is None or e["expires"] > now):
        added.append((e["lat"], e["lon"], limit, direction, KINDS[e["kind"]]))
      continue
    if not len(out):
      continue
    east, north = _meters(out, e["lat"], e["lon"])
    d = np.hypot(east, north)
    i = int(np.argmin(d))
    if d[i] >= MATCH_RADIUS:
      continue
    if e.get("removed"):
      out = np.delete(out, i, axis=0)
    else:
      out[i, 2], out[i, 3] = limit, direction
  return np.vstack([out, np.array(added, dtype=float).reshape(-1, 5)]) if added else out


def load_current(edits: list[dict] | None, now: float) -> np.ndarray:
  return merge(load(current()), edits, now)


def active(edits: list[dict] | None, now: float) -> list[dict]:
  return [e for e in edits or [] if e.get("expires") is None or e["expires"] > now]


def apply_edit(edits: list[dict] | None, edit: dict, now: float) -> list[dict]:
  """One entry per point: a new edit of the same point replaces the old one."""
  kept = [e for e in active(edits, now) if e.get("key") != edit["key"]]
  return (kept + [edit])[-MAX_EDITS:]


def taken(rows: np.ndarray, lat: float, lon: float, direction: float | None, kind: int = RADAR) -> bool:
  """A point of this kind already here for this direction. The other carriageway of a road can be this close, and a bump can sit by a camera."""
  if not len(rows):
    return False
  east, north = _meters(rows, lat, lon)
  close = np.hypot(east, north) < MATCH_RADIUS
  close &= (rows[:, 4] == BUMP) == (kind == BUMP)
  close &= (rows[:, 4] == SIGNAL) == (kind == SIGNAL)  # a red-light camera on a pole the list already has as a speed camera
  if direction is None:
    return bool(close.any())
  return bool((close & (np.isnan(rows[:, 3]) | (angle_diff(rows[:, 3], direction) < DIRECTION_TOLERANCE))).any())


def clean_edits(raw) -> list[dict]:
  """What comes back from a saved copy: known fields only, in range. Anything else stays out."""
  out = []
  for e in raw if isinstance(raw, list) else []:
    try:
      lat, lon, kind, limit, direction = float(e["lat"]), float(e["lon"]), e.get("kind"), e.get("limit"), e.get("direction")
      expires = e.get("expires")
      if not (-90. <= lat <= 90. and -180. <= lon <= 180. and kind in ("edit", *KINDS)):
        continue
      if limit is not None and not valid_limit(int(limit), KINDS.get(kind, RADAR)):
        continue
      if direction is not None and not 0. <= float(direction) <= 360.:
        continue
      out.append({"key": key(lat, lon), "lat": lat, "lon": lon, "kind": kind, "limit": None if limit is None else int(limit),
                  "direction": None if direction is None else float(direction), "both": bool(e.get("both")) and direction is None,
                  "passed": int(e["passed"]) if 0 <= int(e.get("passed") or -1) <= 200 else None,
                  "confirmed": bool(e.get("confirmed")), "removed": bool(e.get("removed")),
                  "who": str(e.get("who", ""))[:40], "t": int(e.get("t") or 0), "expires": None if expires is None else float(expires)})
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
      continue
  return out[-MAX_EDITS:]


def valid_limit(limit, kind: int = RADAR) -> bool:
  if limit is None:
    return True
  low, high = BUMP_SPEEDS if kind == BUMP else CAMERA_LIMITS
  return isinstance(limit, int) and not isinstance(limit, bool) and low <= limit <= high


def alert_lines(hit: dict) -> tuple[str, str]:
  """What the alert says. At a bump the number is the owner's own speed, not a legal limit."""
  kind, limit = hit.get("kind"), hit.get("limit")
  title = f"{KIND_TEXT.get(kind, 'Radar')} a {round(hit.get('distance', 0.) / 10) * 10:.0f} m"
  if not limit:
    return title, ""
  return title, f"Passe a {limit:.0f} km/h" if kind == BUMP else f"Limite {limit:.0f} km/h"


def direction_of(side: str, bearing: float) -> float | None:
  """The stored direction for what the driver picked. Both ways is no direction at all: it warns coming from either side."""
  if side == "mesmo":
    return bearing
  return (bearing + 180.) % 360. if side == "contrario" else None


def relative(direction: float, bearing: float) -> str:
  if math.isnan(direction):
    return "?"
  return "mesmo" if float(angle_diff(direction, bearing)) < DIRECTION_TOLERANCE else "contrario"


def nearby(rows: np.ndarray, edits: list[dict] | None, lat: float, lon: float, bearing: float, reach: float = LIST_REACH) -> list[dict]:
  """The closest points, for the phone: distance and side, never a position."""
  if not len(rows):
    return []
  east, north = _meters(rows, lat, lon)
  dist = np.hypot(east, north)
  mine = {e["key"]: e for e in edits or []}
  off_bearing = angle_diff(np.degrees(np.arctan2(east, north)), bearing)
  ahead = off_bearing < 90
  shown = np.flatnonzero((dist <= reach) & (ahead | (dist <= NEAR_BEHIND)))
  out = []
  for i in shown[np.lexsort((dist[shown], ~ahead[shown]))][:NEAR_LIST]:
    k = key(rows[i, 0], rows[i, 1])
    e = mine.get(k) or next((x for x in mine.values() if abs(x["lat"] - rows[i, 0]) < 3e-4 and abs(x["lon"] - rows[i, 1]) < 3e-4), None)
    out.append({"key": k, "kind": KIND_NAMES.get(int(rows[i, 4]), "radar"), "distance": round(float(dist[i])), "ahead": bool(ahead[i]),
                "limit": None if math.isnan(rows[i, 2]) else round(float(rows[i, 2])), "side": "dois" if e and e.get("both") else relative(rows[i, 3], bearing),
                "confirmed": bool(e and e.get("confirmed")), "mine": bool(e and e.get("kind") in KINDS)})
  return out


last_hit: dict = {}  # what the alert shows: it's built in this same process, on the same frame


def angle_diff(a, b):
  return np.abs((np.asarray(a) - b + 180.) % 360. - 180.)


class RadarWatch:
  """Follows the GPS and says once, per camera, when one is ahead on the car's way."""
  def __init__(self, rows: np.ndarray):
    self.rows = rows
    self.near = rows[:0]
    self.near_id = np.array([], dtype=int)
    self.center: tuple[float, float] | None = None
    self.last_t = -1e9
    self.tracked: int | None = None
    self.tracked_dist = 0.
    self.sightings = 0
    self.warned: set[int] = set()

  def _pick_near(self, lat: float, lon: float) -> None:
    k = math.cos(math.radians(lat))
    d = np.hypot((self.rows[:, 1] - lon) * k, self.rows[:, 0] - lat) * M_PER_DEG
    self.near_id = np.flatnonzero(d < NEAR_RADIUS)
    self.near = self.rows[self.near_id]
    self.center = (lat, lon)

  def update(self, t: float, lat: float, lon: float, bearing: float, speed: float, accuracy: float) -> dict | None:
    if t - self.last_t < STEP_S:
      return None
    self.last_t = t
    if speed < MIN_SPEED or accuracy > MIN_FIX_ACCURACY:
      return None
    k = math.cos(math.radians(lat))
    if self.center is None or math.hypot((lon - self.center[1]) * k, lat - self.center[0]) * M_PER_DEG > NEAR_REFRESH:
      self._pick_near(lat, lon)
    if not len(self.near):
      return None

    east = (self.near[:, 1] - lon) * k * M_PER_DEG
    north = (self.near[:, 0] - lat) * M_PER_DEG
    dist = np.hypot(east, north)
    off = angle_diff(np.degrees(np.arctan2(east, north)), bearing)
    self.warned -= {int(i) for i, d in zip(self.near_id, dist, strict=True) if d > FORGET}
    direction = self.near[:, 3]
    right_way = np.isnan(direction) | (angle_diff(direction, bearing) < DIRECTION_TOLERANCE)
    ahead = dist * np.sin(np.radians(np.minimum(off, 90.))) < MAX_CROSS
    seen = (dist < REACH) & (off < np.where(dist < CLOSE, CONE_CLOSE, CONE)) & ahead & right_way
    if not seen.any():
      self.tracked = None
      return None

    i = np.flatnonzero(seen)[int(np.argmin(dist[seen]))]
    radar = int(self.near_id[i])
    self.sightings = self.sightings + 1 if radar == self.tracked and dist[i] < self.tracked_dist else 1
    self.tracked, self.tracked_dist = radar, float(dist[i])
    limit = None if math.isnan(self.near[i, 2]) else float(self.near[i, 2])
    kind = int(self.near[i, 4]) if self.near.shape[1] > 4 else RADAR
    if radar in self.warned or self.sightings < SIGHTINGS or dist[i] > alert_distance(speed, limit):
      return None
    self.warned.add(radar)
    return {"id": radar, "distance": float(dist[i]), "limit": limit, "kind": kind}
