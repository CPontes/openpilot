"""Speed cameras ahead: the list on the comma and the warning logic. It only warns: nothing here touches the car.

The list comes from OpenStreetMap (c) OpenStreetMap contributors, ODbL. It stays on the comma, so the warning works with
no internet and the car's position goes nowhere. Most cameras have no direction in the data, so one on the other side of
a two-way road can warn too. A warning never stands in for watching the speed.
"""
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
    if radar in self.warned or self.sightings < SIGHTINGS or dist[i] > alert_distance(speed, limit):
      return None
    self.warned.add(radar)
    return {"id": radar, "distance": float(dist[i]), "limit": limit}
