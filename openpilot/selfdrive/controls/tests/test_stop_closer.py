import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.stop_closer import StopCloser, STOP_CLOSER_CONFIRM_TIME, STOP_CLOSER_DISTANCE, STOP_CLOSER_FULL_SPEED, \
                                                         STOP_CLOSER_HARD_BRAKE, STOP_CLOSER_HAVAL_OFFSET, STOP_CLOSER_MAX_DISTANCE, \
                                                         STOP_CLOSER_MAX_SPEED, STOP_CLOSER_RATE_DOWN, STOP_CLOSER_RATE_UP
from openpilot.selfdrive.controls.tests.test_stop_point import FakeMpc, planner  # noqa: F401

DT = 0.05
SLOW = 3.


def lead(d=6.):
  return SimpleNamespace(present=True, dRel=d)


def haval(d=6.):
  # what the Haval's distance reads with the comma seeing the car at d
  return d + float(np.interp(d, *STOP_CLOSER_HAVAL_OFFSET))


def run(sc, seconds, v=SLOW, d=6., hv=None, gas=False, a_prev=0.):
  out = 0.
  for _ in range(round(seconds / DT)):
    out = sc.update(v, haval(d) if hv is None else hv, lead(d), gas, a_prev)
  return out


def full(sc):
  return run(sc, STOP_CLOSER_CONFIRM_TIME + STOP_CLOSER_DISTANCE / STOP_CLOSER_RATE_UP + .5)


class TestWhenItActs:
  def test_slow_with_both_agreeing_it_takes_the_whole_distance(self):
    assert full(StopCloser(DT)) == pytest.approx(STOP_CLOSER_DISTANCE)

  def test_it_comes_slowly(self):
    sc = StopCloser(DT)
    run(sc, STOP_CLOSER_CONFIRM_TIME)
    assert run(sc, .5) <= STOP_CLOSER_RATE_UP * (.5 + DT) + 1e-6  # it starts on the frame the agreement is confirmed

  def test_nothing_above_30_kph(self):
    assert run(StopCloser(DT), 5., v=STOP_CLOSER_MAX_SPEED + .1) == 0.

  def test_less_of_it_between(self):
    v = (STOP_CLOSER_FULL_SPEED + STOP_CLOSER_MAX_SPEED) / 2
    assert run(StopCloser(DT), 5., v=v) == pytest.approx(STOP_CLOSER_DISTANCE / 2, abs=.01)

  def test_far_cars_dont_count(self):
    d = STOP_CLOSER_MAX_DISTANCE + 1.
    assert run(StopCloser(DT), 5., d=d) == 0.


class TestTheHavalHasToAgree:
  def test_without_the_haval_nothing(self):
    assert run(StopCloser(DT), 5., hv=0.) == 0.

  def test_the_haval_somewhere_else_nothing(self):
    # route 31, min 73.5: the model put a motorcycle 30 m farther than the Haval did
    assert run(StopCloser(DT), 5., d=6., hv=haval(6.) + 6.) == 0.
    assert run(StopCloser(DT), 5., d=6., hv=haval(6.) - 6.) == 0.

  def test_without_a_car_nothing(self):
    sc = StopCloser(DT)
    for _ in range(100):
      out = sc.update(SLOW, haval(), SimpleNamespace(present=False, dRel=0.), False, 0.)
    assert out == 0.

  def test_one_frame_either_way_changes_nothing(self):
    sc = StopCloser(DT)
    run(sc, STOP_CLOSER_CONFIRM_TIME - DT)
    assert not sc.on
    sc = StopCloser(DT)
    full(sc)
    run(sc, DT, hv=0.)
    assert sc.on

  def test_disagreeing_it_goes_quickly(self):
    sc = StopCloser(DT)
    full(sc)
    out = run(sc, STOP_CLOSER_CONFIRM_TIME + STOP_CLOSER_DISTANCE / STOP_CLOSER_RATE_DOWN + DT, hv=0.)
    assert out == 0.


class TestItNeverPushes:
  def test_stopped_it_never_grows(self):
    # it would creep up to the car ahead
    assert run(StopCloser(DT), 5., v=0.) == 0.

  def test_stopped_it_keeps_what_it_had(self):
    sc = StopCloser(DT)
    got = full(sc)
    assert run(sc, 5., v=0.) == pytest.approx(got)

  def test_braking_hard_it_never_grows(self):
    assert run(StopCloser(DT), 5., a_prev=STOP_CLOSER_HARD_BRAKE - .1) == 0.

  def test_on_the_gas_it_goes(self):
    sc = StopCloser(DT)
    full(sc)
    assert run(sc, 2., gas=True) == 0.


class TestInThePlanner:
  def msgs(self, v=SLOW, d=6., hv=None, gas=False):
    from opendbc.car.structs import car
    cs = car.CarState.new_message()
    cs.vEgo, cs.vCruise, cs.gasPressed = v, 60., gas
    cs.camLeadDistance = haval(d) if hv is None else hv
    ct = log.ControlsState.new_message()
    ct.longControlState = car.CarControl.Actuators.LongControlState.pid
    rd = log.RadarState.new_message()
    rd.leadOne.present, rd.leadOne.dRel = True, d
    sd = log.SelfdriveState.new_message()
    sd.enabled = True
    m = log.ModelDataV2.new_message()
    m.position.x = np.linspace(0., 18., 33).tolist()
    m.velocity.x = np.full(33, v).tolist()
    return {"carState": cs.as_reader(), "controlsState": ct.as_reader(), "radarState": rd.as_reader(), "selfdriveState": sd.as_reader(),
            "modelV2": m.as_reader()}

  def cp(self, brand="gwm", long=True):
    return SimpleNamespace(brand=brand, openpilotLongitudinalControl=long, longitudinalActuatorDelay=0.25)

  def on(self, planner, **kw):  # noqa: F811
    with patch("openpilot.common.params.Params.get_bool", lambda self, k, *a, **kw: k == "StopCloser"):
      return planner(self.cp(**kw))

  def test_off_by_default(self, planner):  # noqa: F811
    assert planner(self.cp()).stop_closer is None

  def test_other_cars_never_have_it(self, planner):  # noqa: F811
    assert self.on(planner, brand="toyota").stop_closer is None

  def test_not_without_the_comma_longitudinal(self, planner):  # noqa: F811
    assert self.on(planner, long=False).stop_closer is None

  def test_it_reaches_the_mpc(self, planner, monkeypatch):  # noqa: F811
    seen = []
    monkeypatch.setattr(FakeMpc, "update", lambda self, *a, **kw: seen.append(kw.get("lead_extra", 0.)))
    p = self.on(planner)
    for _ in range(100):
      p.update(self.msgs())
    assert seen[-1] == pytest.approx(STOP_CLOSER_DISTANCE)
    assert p.stop_closer_extra == seen[-1]

  def test_it_goes_to_the_log(self, planner):  # noqa: F811
    p = self.on(planner)
    for _ in range(100):
      p.update(self.msgs())
    from unittest.mock import MagicMock
    sm = MagicMock()
    sm.__getitem__ = lambda _, k: self.msgs()[k]
    sm.logMonoTime = {"modelV2": 0}
    sm.all_checks.return_value = True
    pm = MagicMock()
    p.publish(sm, pm)
    assert pm.send.call_args[0][1].longitudinalPlan.stopCloserExtra == pytest.approx(STOP_CLOSER_DISTANCE)

  def test_with_the_longitudinal_off_it_starts_over(self, planner):  # noqa: F811
    p = self.on(planner)
    for _ in range(100):
      p.update(self.msgs())
    off = self.msgs()
    from opendbc.car.structs import car
    ct = log.ControlsState.new_message()
    ct.longControlState = car.CarControl.Actuators.LongControlState.off
    off["controlsState"] = ct.as_reader()
    p.update(off)
    assert p.stop_closer_extra == 0. and p.stop_closer.extra == 0.


def drive(option, hv_error=0., v0=20 / 3.6, gap0=15., lead_brake=2., t_end=30.):
  # the real planner and the compiled MPC, with an ideal car: what counts is the difference between the two runs
  from opendbc.car.structs import car
  from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  from openpilot.selfdrive.controls.radard import _LEAD_ACCEL_TAU
  from openpilot.selfdrive.modeld.constants import ModelConstants
  cp = SimpleNamespace(brand="gwm", openpilotLongitudinalControl=True, longitudinalActuatorDelay=0.25)
  with patch("openpilot.common.params.Params.get_bool", lambda self, k, *a, **kw: option and k == "StopCloser"):
    p = LongitudinalPlanner(cp, init_v=v0)
  x, v, a, xl, vl = 0., v0, 0., gap0, v0
  gaps, accels, fcw = [], [], False
  for k in range(round(t_end / p.dt)):
    vl_new = max(0., v0 - lead_brake * max(0., k * p.dt - 3.))
    rd = log.RadarState.new_message()
    rd.leadOne.dRel, rd.leadOne.vLead, rd.leadOne.vLeadK = float(xl - x), float(vl_new), float(vl_new)
    rd.leadOne.aLeadK, rd.leadOne.aLeadTau, rd.leadOne.present = float((vl_new - vl) / p.dt), float(_LEAD_ACCEL_TAU), True
    vl = vl_new
    cs = car.CarState.new_message()
    cs.vEgo, cs.aEgo, cs.standstill, cs.vCruise = float(v), float(a), bool(v < .01), 50. * 3.6
    cs.camLeadDistance = float(haval(xl - x) + hv_error)
    ct = log.ControlsState.new_message()
    ct.longControlState = LongCtrlState.pid
    sd = log.SelfdriveState.new_message()
    sd.enabled, sd.personality = True, log.LongitudinalPersonality.aggressive
    m = log.ModelDataV2.new_message()
    m.position.x = ((v + .5) * np.array(ModelConstants.T_IDXS)).tolist()
    m.velocity.x = [float(v)] + [float(v + .5)] * (len(ModelConstants.T_IDXS) - 1)
    m.acceleration.x = [0.] * len(ModelConstants.T_IDXS)
    m.action.desiredAcceleration = float(a + .5)
    p.update({"carState": cs.as_reader(), "controlsState": ct.as_reader(), "radarState": rd.as_reader(), "selfdriveState": sd.as_reader(),
              "modelV2": m.as_reader()})
    a = min(-.5, p.output_a_target) if p.output_should_stop else p.output_a_target
    v = max(0., v + a * p.dt)
    a = a if v > 0. else 0.
    x += v * p.dt
    xl += vl * p.dt
    gaps.append(xl - x)
    accels.append(a)
    fcw |= p.fcw
  return gaps[-1], min(gaps), min(accels), fcw


class TestWithTheSolver:
  @pytest.fixture(autouse=True)
  def solver(self, monkeypatch):
    pytest.importorskip("openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.c_generated_code.acados_ocp_solver_pyx")
    # the planner fixture above may have left a planner built on the fake MPC
    monkeypatch.delitem(sys.modules, "openpilot.selfdrive.controls.lib.longitudinal_planner", raising=False)
    monkeypatch.delitem(sys.modules, "openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc", raising=False)

  def test_it_stops_closer_and_no_harder(self):
    before, now = drive(False), drive(True)
    assert before[0] - now[0] > 1.
    assert now[0] > 2.
    assert now[2] >= before[2] - .1
    assert not before[3] and not now[3]

  def test_a_hard_stop_keeps_most_of_the_margin(self):
    before, now = drive(False, v0=50 / 3.6, gap0=25., lead_brake=4.), drive(True, v0=50 / 3.6, gap0=25., lead_brake=4.)
    assert before[1] - now[1] < .8
    assert now[2] >= before[2] - .1

  def test_with_the_haval_somewhere_else_it_is_the_same_as_without_it(self):
    assert drive(True, hv_error=6.) == pytest.approx(drive(False))
