import math
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.stop_point import StopPoint, STOP_POINT_ACCEL_MIN, STOP_POINT_JERK

DT = 0.05


def model(stops_in=20., end_speed=0., action=-0.5):
  return SimpleNamespace(position=SimpleNamespace(x=[0., stops_in]), velocity=SimpleNamespace(x=[5., end_speed]),
                         action=SimpleNamespace(desiredAcceleration=action))


def run(sp, seconds, v=6., lead=False, gas=False, **kwargs):
  out = math.inf
  for _ in range(int(seconds / DT)):
    out = sp.update(v, lead, gas, model(**kwargs))
  return out


class TestStopPoint:
  def setup_method(self):
    self.sp = StopPoint(DT)

  def test_waits_half_a_second(self):
    assert run(self.sp, 0.4) == math.inf
    assert run(self.sp, 0.2) < 0.

  def test_asks_for_what_the_point_takes(self):
    assert run(self.sp, 5., v=6., stops_in=19.) == pytest.approx(-1., abs=0.01)

  def test_builds_up_slowly(self):
    run(self.sp, 0.5)
    first = self.sp.accel
    self.sp.update(10., False, False, model(stops_in=10.))
    assert first - self.sp.accel == pytest.approx(STOP_POINT_JERK * DT, abs=1e-6)

  def test_never_harder_than_the_limit(self):
    assert run(self.sp, 10., v=12., stops_in=8.) == STOP_POINT_ACCEL_MIN

  def test_the_end_of_the_stop_is_no_jolt(self):
    assert run(self.sp, 5., v=2., stops_in=0.5, action=-0.2) == math.inf  # slow, and it never started
    run(self.sp, 1.)
    assert run(self.sp, 5., v=1.5, stops_in=0.5, action=-0.1) >= -0.6

  @pytest.mark.parametrize("kwargs", [{"lead": True}, {"gas": True}, {"end_speed": 2.}, {"action": -0.2}, {"v": 13.}, {"v": 2.}])
  def test_only_when_all_of_it_holds(self, kwargs):
    assert run(self.sp, 3., **kwargs) == math.inf

  def test_a_plan_that_stops_stopping_ends_it(self):
    run(self.sp, 2.)
    assert self.sp.update(6., False, False, model(end_speed=5.)) == math.inf
    assert self.sp.time == 0.

  def test_an_empty_plan_is_nothing(self):
    empty = SimpleNamespace(position=SimpleNamespace(x=[]), velocity=SimpleNamespace(x=[]), action=SimpleNamespace(desiredAcceleration=-1.))
    assert self.sp.update(6., False, False, empty) == math.inf


class FakeMpc:
  def __init__(self, dt=0.05):
    self.source = log.LongitudinalPlan.LongitudinalPlanSource.cruise
    self.solve_time = 0.
    self.crash_cnt = 0
    self.v_solution = np.full(13, 6.)
    self.a_solution = np.zeros(13)
    self.j_solution = np.zeros(12)

  def set_weights(self, *args, **kwargs):
    pass

  def set_cur_state(self, *args):
    pass

  def update(self, *args, **kwargs):
    pass


@pytest.fixture
def planner(monkeypatch):
  # the solver isn't built here. What matters is that the real planner reads real messages
  fake = ModuleType("openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc")
  fake.LongitudinalMpc = FakeMpc
  fake.LongitudinalPlanSource = log.LongitudinalPlan.LongitudinalPlanSource
  fake.T_IDXS = np.linspace(0., 10., 13)
  monkeypatch.setitem(sys.modules, fake.__name__, fake)
  monkeypatch.delitem(sys.modules, "openpilot.selfdrive.controls.lib.longitudinal_planner", raising=False)
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  return LongitudinalPlanner


def stopping_plan():
  m = log.ModelDataV2.new_message()
  m.position.x = np.linspace(0., 18., 33).tolist()
  m.velocity.x = np.linspace(6., 0., 33).tolist()
  m.action.desiredAcceleration = -0.5
  return m


class TestInThePlanner:
  def sm(self):
    from opendbc.car.structs import car
    cs = car.CarState.new_message()
    cs.vEgo = 6.
    cs.vCruise = 60.
    ct = log.ControlsState.new_message()
    ct.longControlState = car.CarControl.Actuators.LongControlState.pid
    rd = log.RadarState.new_message()
    sd = log.SelfdriveState.new_message()
    sd.enabled = True
    return {"carState": cs.as_reader(), "controlsState": ct.as_reader(), "radarState": rd.as_reader(), "selfdriveState": sd.as_reader(),
            "modelV2": stopping_plan().as_reader()}

  def cp(self, brand):
    return SimpleNamespace(brand=brand, openpilotLongitudinalControl=True, longitudinalActuatorDelay=0.25)

  def test_the_stop_point_brakes_harder_than_the_model(self, planner):
    p = planner(self.cp("gwm"))
    for _ in range(100):
      p.update(self.sm())
    assert p.output_a_target < -0.8

  def test_other_cars_keep_the_model(self, planner):
    p = planner(self.cp("toyota"))
    for _ in range(100):
      p.update(self.sm())
    assert p.output_a_target == pytest.approx(-0.5, abs=0.05)
