import math
from types import SimpleNamespace as NS

from openpilot.selfdrive.hexa import sensors, timeline


def test_grade_is_positive_uphill():
  assert abs(sensors.grade_pct(math.atan(0.08)) - 8.) < 1e-9 and sensors.grade_pct(-math.atan(0.05)) < 0


def test_bumps_and_roughness_whatever_way_the_comma_is_mounted():
  for up in (0, 2):  # gravity on x or on z
    c = sensors.CarSensors()
    c.on_car(15., 0., 0., None, 0.01)
    for i in range(1040):  # 10 s at 104 Hz, one jolt at 5 s
      t = i / 104
      xyz = [0.1, 0.2, 0.3]
      xyz[up] = 9.81 + (4.5 if 5.0 <= t < 5.03 else 0.)
      c.on_accel(t, tuple(xyz))
    r = c.result()
    assert r["se_bumps"] == 1 and r["se_rough_t"] > 9.5 and r["se_rough_ss"] > 0


def test_only_while_moving():
  c = sensors.CarSensors()
  c.on_car(0.5, 0., 0., None, 0.01)
  for i in range(300):
    c.on_accel(i / 104, (0., 0., 9.81 + (5. if i == 150 else 0.)))
  c.on_pose(1., 0.2, 0.2)
  assert c.result()["se_bumps"] == 0 and c.result()["se_grade_up"] == 0


def test_grade_lean_and_braking():
  c = sensors.CarSensors()
  c.on_car(15., -3.2, 0., None, 0.01)
  for i in range(300):
    c.on_pose(i / 100, math.radians(4.), math.atan(0.12))
  r = c.result()
  assert 11.5 < r["se_grade_up"] <= 12.0 and r["se_grade_down"] == 0 and abs(r["se_roll"] - 4.) < 1e-6 and r["se_decel"] == 3.2


def test_wheel_spread_only_straight_and_steady():
  c = sensors.CarSensors()
  for _ in range(100):
    c.on_car(20., 0., 1., (20.2, 20., 20., 20.), 0.01)  # one tire turning 1 % faster
  c.on_car(20., 0., 10., (25., 20., 20., 20.), 0.01)  # turning: left out
  r = c.result()
  assert abs(r["se_wheel_s"] / r["se_wheel_t"] - 0.998) < 0.01


def test_a_drive_merges_its_minutes():
  total = {}
  sensors.merge(total, {"se_bumps": 2, "se_grade_up": 8., "se_v12_min": 12.4, "se_rough_t": 50.})
  sensors.merge(total, {"se_bumps": 1, "se_grade_up": 5., "se_v12_min": 0., "se_rough_t": 60.})
  sensors.merge(total, {"se_bumps": 0, "se_grade_up": 9., "se_v12_min": 12.1})
  assert (total["se_bumps"], total["se_grade_up"], total["se_v12_min"], total["se_rough_t"]) == (3, 9., 12.1, 110.)


def test_live_reading():
  cc = NS(orientationNED=[math.radians(2.), math.atan(-0.06), 0.])
  ws = NS(fl=20., fr=20.2, rl=20., rr=20.)
  r = sensors.live(cc, NS(voltage=12650), NS(hasFix=True, altitude=812.4), NS(wheelSpeeds=ws))
  assert r == {"grade": -6.0, "roll": 2.0, "v12": 12.7, "alt": 812, "wheels": 1.0}
  r = sensors.live(NS(orientationNED=[]), None, NS(hasFix=False, altitude=0.), NS(wheelSpeeds=NS(fl=1., fr=1., rl=1., rr=1.)))
  assert r == {"grade": None, "roll": None, "v12": None, "alt": None, "wheels": None}


def test_trip_segment_reads_the_sensors_from_real_messages():
  from openpilot.cereal import log
  NS_ = 1_000_000_000
  events = []

  def add(service, t, fill):
    ev = log.Event.new_message(logMonoTime=int(1_000 * NS_ + t * NS_), valid=True)
    fill(ev.init(service))
    events.append(ev.as_reader())

  for i in range(1000):
    t = i / 100
    add("carState", t, lambda c: setattr(c, "vEgo", 15.))
    add("carControl", t, lambda c: setattr(c, "orientationNED", [0., math.atan(0.07), 0.]))
    add("accelerometer", t, lambda a, t=t: setattr(a.acceleration, "v", [0., 0., 9.81 + (5. if 5. <= t < 5.03 else 0.)]))
    if i % 50 == 0:
      add("peripheralState", t, lambda p: setattr(p, "voltage", 12500))
  s = timeline.trip_segment(events)["summary"]
  assert s["se_bumps"] == 1 and 6.5 < s["se_grade_up"] <= 7.0 and s["se_v12_min"] == 12.5
