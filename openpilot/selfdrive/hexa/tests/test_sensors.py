import math
from types import SimpleNamespace as NS

from openpilot.selfdrive.hexa import sensors, timeline


def test_grade_is_positive_uphill():
  assert abs(sensors.grade_pct(math.atan(0.08)) - 8.) < 1e-9 and sensors.grade_pct(-math.atan(0.05)) < 0


def test_only_while_moving():
  c = sensors.CarSensors()
  c.on_car(0.5, 0., 0., None, 0.01)
  c.on_pose(1., 0.2, 0.2)
  c.on_motion(1.)
  assert c.result()["se_lat"] == 0 and c.result()["se_grade_up"] == 0


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
  sensors.merge(total, {"se_wheel_t": 50., "se_grade_up": 8., "se_v12_min": 12.4})
  sensors.merge(total, {"se_wheel_t": 60., "se_grade_up": 5., "se_v12_min": 0.})
  sensors.merge(total, {"se_grade_up": 9., "se_v12_min": 12.1})
  assert (total["se_wheel_t"], total["se_grade_up"], total["se_v12_min"]) == (110., 9., 12.1)


def test_live_reading():
  cc = NS(orientationNED=[math.radians(2.), math.atan(-0.06), 0.])
  ws = NS(fl=20., fr=20.2, rl=20., rr=20.)
  r = sensors.live(cc, NS(voltage=12650), NS(hasFix=True, altitude=812.4), NS(wheelSpeeds=ws))
  assert r == {"grade": -6.0, "roll": 2.0, "v12": 12.7, "wheels": 1.0}
  r = sensors.live(NS(orientationNED=[]), None, NS(hasFix=False, altitude=0.), NS(wheelSpeeds=NS(fl=1., fr=1., rl=1., rr=1.)))
  assert r == {"grade": None, "roll": None, "v12": None, "wheels": None}


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
    add("deviceMotion", t, lambda d: setattr(d.angularVelocityDevice, "z", -.2))
    if i % 50 == 0:
      add("peripheralState", t, lambda p: setattr(p, "voltage", 12500))
  s = timeline.trip_segment(events)["summary"]
  assert abs(s["se_lat"] - 3.) < 1e-6 and 6.5 < s["se_grade_up"] <= 7.0 and s["se_v12_min"] == 12.5


def test_the_force_in_a_curve_comes_from_speed_and_yaw_rate():
  c = sensors.CarSensors()
  c.on_car(10., 0., 0., None, .1)
  c.on_motion(.3)
  assert abs(c.result()["se_lat"] - 3.) < 1e-9


def test_the_phone_gets_no_altitude():
  gps = NS(hasFix=True, altitude=20.)
  assert "alt" not in sensors.live(NS(orientationNED=[0., 0., 0.]), None, gps, None)


# ----- automatic marks (owner's decision, 02/11)

def test_a_very_hard_brake_is_one_event():
  c = sensors.CarSensors()
  for i in range(40):  # 0.4 s braking at -7 m/s²
    c.on_car(15. - i * .07, -7., 0., None, .01, 1. + i * .01)
  assert [k for _, k in c.events] == ["hardbrake"]
  assert c.result()["se_hard_brakes"] == 1


def test_a_normal_hard_brake_is_not():
  c = sensors.CarSensors()
  for i in range(100):  # the Haval ACC's hard brakes are about -3
    c.on_car(15., -4.5, 0., None, .01, i * .01)
  for i in range(20):  # -7 for 0.2 s only
    c.on_car(15., -7., 0., None, .01, 2. + i * .01)
  assert c.events == []


def test_the_summary_data_has_no_hit_to_see():
  # a hit lasts about 0.1 s and the qlog has one IMU sample a second: the mark would be a coin toss, so it doesn't exist
  from cereal.services import SERVICE_LIST
  imu = SERVICE_LIST["accelerometer"]
  assert imu.frequency / imu.decimation <= 1.
  assert not hasattr(sensors.CarSensors, "on_accel")
