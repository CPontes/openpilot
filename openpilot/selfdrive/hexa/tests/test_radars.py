import json
import math

import numpy as np
import pytest

from openpilot.selfdrive.hexa import radars

NAN = float("nan")
LAT, LON = -22.9, -43.1
K = math.cos(math.radians(LAT))


def at(north=0., east=0.):
  return LAT + north / radars.M_PER_DEG, LON + east / (radars.M_PER_DEG * K)


def rows(*items):
  return np.array([[*at(n, e), lim, d] for n, e, lim, d in items], dtype=float)


def drive(watch, start=-1500., end=300., speed=16.7, bearing=0., east=0., t0=0.):
  """Straight north, one GPS fix each 0.1 s. Returns what was said and where."""
  out = []
  t, n = t0, start
  while n < end:
    lat, lon = at(n, east)
    r = watch.update(t, lat, lon, bearing, speed, 3.)
    if r:
      out.append((n, r))
    t += .1
    n += speed * .1
  return out


@pytest.mark.parametrize("kmh, limit, expected", [(80, 60, 333), (90, 60, 472), (100, 80, 411), (120, 80, 767), (150, 100, 1140)])
def test_warns_early_enough_to_slow_down_gently(kmh, limit, expected):
  assert radars.alert_distance(kmh / 3.6, limit) == pytest.approx(expected, abs=1)


def test_distance_has_limits_and_needs_no_limit_known():
  assert radars.alert_distance(150 / 3.6, 40) == radars.MAX_DISTANCE
  assert radars.alert_distance(5., 40) == radars.MIN_DISTANCE
  assert radars.alert_distance(100 / 3.6, None) == pytest.approx(333, abs=1)
  assert radars.alert_distance(30 / 3.6, 60) == radars.MIN_DISTANCE
  # just over the limit never warns later than just under it
  assert radars.alert_distance(60.5 / 3.6, 60) >= radars.alert_distance(59.5 / 3.6, 60)


def test_camera_ahead_warns_once_with_distance_and_limit():
  out = drive(radars.RadarWatch(rows((0, 0, 60, NAN))))
  assert len(out) == 1
  n, r = out[0]
  assert r["limit"] == 60 and -n == pytest.approx(r["distance"], abs=3) and 140 < r["distance"] < 230


def test_over_the_limit_warns_sooner():
  slow = drive(radars.RadarWatch(rows((0, 0, 60, NAN))), speed=16.)[0][1]["distance"]
  fast = drive(radars.RadarWatch(rows((0, 0, 60, NAN))), speed=25.)[0][1]["distance"]
  assert fast > 2 * slow


def test_parallel_street_stays_quiet():
  assert drive(radars.RadarWatch(rows((0, 0, 60, NAN))), east=120.) == []


def test_camera_behind_stays_quiet():
  assert drive(radars.RadarWatch(rows((-200, 0, 60, NAN))), start=-100.) == []


def test_camera_facing_the_other_way_stays_quiet():
  assert drive(radars.RadarWatch(rows((0, 0, 60, 180.)))) == []
  assert len(drive(radars.RadarWatch(rows((0, 0, 60, 10.))))) == 1


def test_unknown_limit_still_warns():
  assert drive(radars.RadarWatch(rows((0, 0, NAN, NAN))))[0][1]["limit"] is None


def test_needs_speed_and_a_good_fix():
  watch = radars.RadarWatch(rows((0, 0, 60, NAN)))
  assert drive(watch, speed=4.) == []
  t, n = 0., -300.
  while n < 0:
    lat, lon = at(n)
    assert watch.update(t, lat, lon, 0., 16., 80.) is None
    t += .1
    n += 1.6


def test_warns_again_on_another_pass():
  watch = radars.RadarWatch(rows((0, 0, 60, NAN)))
  assert len(drive(watch)) == 1
  assert drive(watch, start=-400., t0=1000.) == []
  drive(watch, start=2800., end=3000., t0=2000.)
  assert len(drive(watch, start=-400., t0=3000.)) == 1


def test_empty_list_does_nothing():
  assert drive(radars.RadarWatch(np.zeros((0, 4)))) == []


def test_looks_once_a_second():
  watch = radars.RadarWatch(rows((0, 0, 60, NAN)))
  lat, lon = at(-100.)
  watch.update(10., lat, lon, 0., 16., 3.)
  assert watch.last_t == 10.
  watch.update(10.5, lat, lon, 0., 16., 3.)
  assert watch.last_t == 10.


def test_parse_overpass():
  data = {"elements": [
    {"type": "node", "lat": -22.1, "lon": -43.1, "tags": {"highway": "speed_camera", "maxspeed": "60", "direction": "NE"}},
    {"type": "node", "lat": -22.2, "lon": -43.2, "tags": {"maxspeed": "BR:urban", "direction": "forward"}},
    {"type": "node", "lat": -22.3, "lon": -43.3, "tags": {"maxspeed": "80 km/h", "direction": "270"}},
    {"type": "node", "lat": -22.4, "lon": -43.4, "tags": {"maxspeed": "999"}},
    {"type": "way", "id": 1},
  ]}
  r = {round(x[0], 1): x for x in radars.parse_overpass(data)}
  assert r[-22.1][2] == 60 and r[-22.1][3] == 45
  assert math.isnan(r[-22.2][2]) and math.isnan(r[-22.2][3])
  assert r[-22.3][2] == 80 and r[-22.3][3] == 270
  assert math.isnan(r[-22.4][2]) and len(r) == 4


def test_save_and_load(tmp_path):
  p = tmp_path / "r.csv"
  radars.save(rows((0, 0, 60, NAN), (10, 0, NAN, 90.)), p)
  r = radars.load(p)
  assert r.shape == (2, 4) and r[0, 2] == 60 and math.isnan(r[1, 2]) and r[1, 3] == 90


def test_bundled_list_loads_and_stays_in_brazil():
  r = radars.load(radars.BUNDLED)
  assert len(r) > radars.MIN_RADARS
  assert r[:, 0].min() > -35 and r[:, 0].max() < 6 and r[:, 1].min() > -75 and r[:, 1].max() < -30
  assert "OpenStreetMap" in radars.BUNDLED.read_text()[:120]


class Answer:
  def __init__(self, body):
    self.body = body

  def __enter__(self):
    return self

  def __exit__(self, *a):
    return False

  def read(self, n):
    return self.body[:n]


def test_download_keeps_the_old_list_on_a_bad_answer(tmp_path, monkeypatch):
  p = tmp_path / "r.csv"
  p.write_text("velha")
  for body in (json.dumps({"elements": []}).encode(), b"x" * (radars.MAX_DOWNLOAD + 2), b"<html>erro</html>"):
    monkeypatch.setattr(radars.urllib.request, "urlopen", lambda *a, b=body, **k: Answer(b))
    with pytest.raises(ValueError):
      radars.download(p)
    assert p.read_text() == "velha"


def test_download_swaps_a_good_list(tmp_path, monkeypatch):
  p = tmp_path / "r.csv"
  elements = [{"type": "node", "lat": -20 - i / 1000, "lon": -40., "tags": {"maxspeed": "60"}} for i in range(radars.MIN_RADARS)]
  monkeypatch.setattr(radars.urllib.request, "urlopen", lambda *a, **k: Answer(json.dumps({"elements": elements}).encode()))
  assert radars.download(p) == radars.MIN_RADARS
  assert len(radars.load(p)) == radars.MIN_RADARS
