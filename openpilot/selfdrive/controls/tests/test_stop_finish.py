import math
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.stop_finish import StopFinish, STOP_FINISH_ACCEL, STOP_FINISH_ARM_ACTION, STOP_FINISH_ARM_SPEED, \
                                                         STOP_FINISH_END_SPEED, STOP_FINISH_GO_ACTION, STOP_FINISH_GO_TIME, STOP_FINISH_LEAD_CONFIRM, \
                                                         STOP_FINISH_LEAD_DISTANCE, STOP_FINISH_LEAD_TIME, STOP_FINISH_RISE_TIME, STOP_FINISH_SPEED
from openpilot.selfdrive.controls.tests.test_stop_point import planner  # noqa: F401

DT = 0.05
NO_LEAD = SimpleNamespace(present=False, dRel=0.)


def model(action=-1., end=0.):
  return SimpleNamespace(action=SimpleNamespace(desiredAcceleration=action), velocity=SimpleNamespace(x=[5., end]))


def lead(d=8.):
  return SimpleNamespace(present=True, dRel=d)


def arm(sf):
  assert sf.update(STOP_FINISH_ARM_SPEED + .1, NO_LEAD, False, model()) == math.inf
  assert sf.armed


def hold(sf):
  arm(sf)
  assert sf.update(STOP_FINISH_SPEED - .05, NO_LEAD, False, model(action=-.4)) == STOP_FINISH_ACCEL
  assert sf.holding


def see_lead(sf, v=2., d=8.):
  for _ in range(round(STOP_FINISH_LEAD_CONFIRM / DT)):
    sf.update(v, lead(d), False, model())


class TestStopFinish:
  def setup_method(self):
    self.sf = StopFinish(DT)

  def test_the_red_light_of_the_log(self):
    # route 2e, minute 12.9: down from 30 km/h, then +0.55 m/s^2 at 1.3 km/h, past the crosswalk
    for v, a in ((8.3, -.8), (5., -1.6), (3., -1.1), (1.5, -.7), (.8, -.4), (.5, 0.), (.36, .5)):
      out = self.sf.update(v, NO_LEAD, False, model(action=a))
    assert out == STOP_FINISH_ACCEL

  def test_nothing_without_the_model_braking(self):
    self.sf.update(6., NO_LEAD, False, model(action=STOP_FINISH_ARM_ACTION))
    assert not self.sf.armed

  def test_arms_at_the_edge_of_the_braking(self):
    self.sf.update(6., NO_LEAD, False, model(action=STOP_FINISH_ARM_ACTION - .01))
    assert self.sf.armed

  def test_nothing_from_a_crawl(self):
    self.sf.update(STOP_FINISH_ARM_SPEED - .01, NO_LEAD, False, model())
    assert not self.sf.armed

  def test_arms_at_the_edge_of_the_speed(self):
    self.sf.update(STOP_FINISH_ARM_SPEED, NO_LEAD, False, model())
    assert self.sf.armed

  def test_nothing_if_the_plan_goes_on(self):
    self.sf.update(6., NO_LEAD, False, model(end=STOP_FINISH_END_SPEED))
    assert not self.sf.armed

  def test_arms_at_the_edge_of_the_plan(self):
    self.sf.update(6., NO_LEAD, False, model(end=STOP_FINISH_END_SPEED - .01))
    assert self.sf.armed

  def test_an_empty_plan_is_nothing(self):
    empty = SimpleNamespace(action=SimpleNamespace(desiredAcceleration=-1.), velocity=SimpleNamespace(x=[]))
    self.sf.update(6., NO_LEAD, False, empty)
    assert not self.sf.armed

  def test_not_yet_above_the_speed(self):
    arm(self.sf)
    assert self.sf.update(STOP_FINISH_SPEED, NO_LEAD, False, model(action=-.4)) == math.inf

  def test_a_bump_slows_down_and_goes_on(self):
    arm(self.sf)
    for _ in range(round(STOP_FINISH_RISE_TIME / DT)):
      self.sf.update(STOP_FINISH_SPEED, NO_LEAD, False, model(action=.2))
    assert not self.sf.armed
    assert self.sf.update(STOP_FINISH_SPEED - .1, NO_LEAD, False, model(action=-.1)) == math.inf

  def test_a_frame_of_more_does_not_disarm(self):
    # a hesitation on the way down, at the red light it went from 0 to +0.55 in 1.4 s
    arm(self.sf)
    for _ in range(round(STOP_FINISH_RISE_TIME / DT) - 1):
      self.sf.update(2., NO_LEAD, False, model(action=.01))
    self.sf.update(2., NO_LEAD, False, model(action=0.))
    self.sf.update(2., NO_LEAD, False, model(action=.01))
    assert self.sf.armed

  def test_below_the_speed_the_rise_does_not_disarm(self):
    arm(self.sf)
    for _ in range(round(STOP_FINISH_RISE_TIME / DT) - 1):
      self.sf.update(2., NO_LEAD, False, model(action=.5))
    assert self.sf.update(STOP_FINISH_SPEED - .1, NO_LEAD, False, model(action=.5)) == STOP_FINISH_ACCEL

  def test_above_it_arming_beats_the_rise(self):
    # the same frame can't be both: braking at speed is armed whatever came before
    for _ in range(round(STOP_FINISH_RISE_TIME / DT) + 1):
      self.sf.update(6., NO_LEAD, False, model(action=.5))
    self.sf.update(6., NO_LEAD, False, model())
    assert self.sf.armed

  def test_below_the_speed_a_change_of_mind_does_not_disarm(self):
    arm(self.sf)
    assert self.sf.update(STOP_FINISH_SPEED - .1, NO_LEAD, False, model(action=.5)) == STOP_FINISH_ACCEL

  def test_the_hold_outlasts_the_model(self):
    hold(self.sf)
    for _ in range(int(120 / DT)):
      assert self.sf.update(0., NO_LEAD, False, model(action=1.)) == STOP_FINISH_ACCEL

  def test_the_gas_lets_go(self):
    hold(self.sf)
    assert self.sf.update(0., NO_LEAD, True, model(action=1.)) == math.inf
    assert not self.sf.holding and not self.sf.armed
    assert self.sf.update(0., NO_LEAD, False, model(action=1.)) == math.inf

  def test_the_gas_before_the_stop_disarms(self):
    arm(self.sf)
    self.sf.update(2., NO_LEAD, True, model())
    assert self.sf.update(.5, NO_LEAD, False, model()) == math.inf

  def test_a_car_ahead_is_the_lead_logic(self):
    arm(self.sf)
    see_lead(self.sf, d=STOP_FINISH_LEAD_DISTANCE - .1)
    assert not self.sf.armed
    # it leaves before we stop: following it is not up to this
    for _ in range(int(5 / DT)):
      assert self.sf.update(.5, NO_LEAD, False, model()) == math.inf

  def test_a_target_far_out_does_not_count(self):
    arm(self.sf)
    for _ in range(40):
      self.sf.update(2., lead(STOP_FINISH_LEAD_DISTANCE), False, model())
    assert self.sf.armed

  def test_not_there_is_not_a_lead(self):
    arm(self.sf)
    for _ in range(40):
      self.sf.update(2., SimpleNamespace(present=False, dRel=5.), False, model())
    assert self.sf.armed

  def test_a_blip_close_by_does_not_disarm(self):
    # at that same light, a target came and went at 17 m before the pedal and at 7 m after it
    arm(self.sf)
    for _ in range(round(STOP_FINISH_LEAD_CONFIRM / DT) - 1):
      self.sf.update(1.5, lead(), False, model())
    assert self.sf.update(1.5, NO_LEAD, False, model()) == math.inf
    assert self.sf.update(.5, NO_LEAD, False, model()) == STOP_FINISH_ACCEL

  def test_blips_do_not_add_up(self):
    arm(self.sf)
    for _ in range(4):
      for _ in range(round(STOP_FINISH_LEAD_CONFIRM / DT) - 1):
        self.sf.update(1.5, lead(), False, model())
      self.sf.update(1.5, NO_LEAD, False, model())
    assert self.sf.armed

  def test_no_arming_until_the_car_ahead_is_really_gone(self):
    see_lead(self.sf, v=6.)
    for _ in range(round(STOP_FINISH_LEAD_TIME / DT) - 1):
      self.sf.update(6., NO_LEAD, False, model())
      assert not self.sf.armed
    self.sf.update(6., NO_LEAD, False, model())
    assert self.sf.armed

  def test_the_gas_does_not_forget_the_car_ahead(self):
    see_lead(self.sf, v=6.)
    self.sf.update(6., NO_LEAD, True, model())
    self.sf.update(6., NO_LEAD, False, model())
    assert not self.sf.armed

  def test_the_gas_still_sees_the_car_ahead(self):
    for _ in range(round(STOP_FINISH_LEAD_CONFIRM / DT)):
      self.sf.update(6., lead(), True, model())
    self.sf.update(6., NO_LEAD, False, model())
    assert not self.sf.armed

  def test_a_car_that_shows_up_during_the_hold_changes_nothing(self):
    hold(self.sf)
    see_lead(self.sf, v=0.)
    assert self.sf.holding
    assert self.sf.update(0., lead(), False, model(action=1.)) == STOP_FINISH_ACCEL

  def test_the_brake_does_not_end_the_hold(self):
    hold(self.sf)
    self.sf.long_off(0., False)
    assert self.sf.update(0., NO_LEAD, False, model(action=1.)) == STOP_FINISH_ACCEL

  def test_the_gas_with_the_longitudinal_off_ends_it(self):
    hold(self.sf)
    self.sf.long_off(0., True)
    assert self.sf.update(0., NO_LEAD, False, model(action=1.)) == math.inf

  def test_moving_with_the_longitudinal_off_ends_it(self):
    hold(self.sf)
    self.sf.long_off(STOP_FINISH_SPEED, False)
    assert not self.sf.holding

  def test_just_under_the_speed_it_stays(self):
    hold(self.sf)
    self.sf.long_off(STOP_FINISH_SPEED - .01, False)
    assert self.sf.holding

  def test_the_longitudinal_off_disarms(self):
    arm(self.sf)
    self.sf.long_off(2., False)
    assert not self.sf.armed and not self.sf.holding

  def test_the_alert_comes_back_after_the_brake(self):
    hold(self.sf)
    for _ in range(round(STOP_FINISH_GO_TIME / DT) - 1):
      self.sf.update(0., NO_LEAD, False, model(action=1.))
    self.sf.long_off(0., False)
    self.sf.update(0., NO_LEAD, False, model(action=1.))
    assert not self.sf.go_asked

  def test_tells_when_the_model_asks_to_go(self):
    hold(self.sf)
    for _ in range(round(STOP_FINISH_GO_TIME / DT) - 1):
      self.sf.update(0., NO_LEAD, False, model(action=STOP_FINISH_GO_ACTION + .01))
      assert not self.sf.go_asked
    self.sf.update(0., NO_LEAD, False, model(action=STOP_FINISH_GO_ACTION + .01))
    assert self.sf.go_asked

  def test_a_model_that_hesitates_starts_over(self):
    hold(self.sf)
    for _ in range(round(STOP_FINISH_GO_TIME / DT) - 1):
      self.sf.update(0., NO_LEAD, False, model(action=1.))
    self.sf.update(0., NO_LEAD, False, model(action=STOP_FINISH_GO_ACTION))
    self.sf.update(0., NO_LEAD, False, model(action=1.))
    assert not self.sf.go_asked

  def test_asked_stays_until_the_gas(self):
    hold(self.sf)
    for _ in range(round(STOP_FINISH_GO_TIME / DT)):
      self.sf.update(0., NO_LEAD, False, model(action=1.))
    self.sf.update(0., NO_LEAD, False, model(action=0.))
    assert self.sf.go_asked
    self.sf.update(0., NO_LEAD, True, model())
    assert not self.sf.go_asked

  def test_reset_lets_go(self):
    hold(self.sf)
    self.sf.reset()
    assert self.sf.update(0., NO_LEAD, False, model(action=1.)) == math.inf

  def test_reset_clears_the_counts(self):
    arm(self.sf)
    self.sf.update(2., NO_LEAD, False, model(action=.5))
    self.sf.reset()
    assert self.sf.rise_time == 0.
    hold(self.sf)
    self.sf.update(0., NO_LEAD, False, model(action=1.))
    self.sf.reset()
    assert self.sf.go_time == 0.


class TestInThePlanner:
  def msgs(self, v, action, end=0., gas=False, control=None):
    from opendbc.car.structs import car
    cs = car.CarState.new_message()
    cs.vEgo = v
    cs.vCruise = 60.
    cs.gasPressed = gas
    ct = log.ControlsState.new_message()
    ct.longControlState = control if control is not None else car.CarControl.Actuators.LongControlState.pid
    m = log.ModelDataV2.new_message()
    m.position.x = np.linspace(0., 18., 33).tolist()
    m.velocity.x = np.linspace(max(v, .1), end, 33).tolist()
    m.action.desiredAcceleration = action
    sd = log.SelfdriveState.new_message()
    sd.enabled = True
    return {"carState": cs.as_reader(), "controlsState": ct.as_reader(), "radarState": log.RadarState.new_message().as_reader(),
            "selfdriveState": sd.as_reader(), "modelV2": m.as_reader()}

  def cp(self, brand="gwm"):
    return SimpleNamespace(brand=brand, openpilotLongitudinalControl=True, longitudinalActuatorDelay=0.25)

  def on(self, planner, brand="gwm"):  # noqa: F811
    from unittest.mock import patch
    with patch("openpilot.common.params.Params.get_bool", lambda self, k, *a, **kw: k == "StopFinish"):
      return planner(self.cp(brand))

  def held(self, p):
    p.update(self.msgs(6., -1.))
    p.update(self.msgs(.8, -.4))
    p.update(self.msgs(.1, .5))
    return p

  def test_off_by_default(self, planner):  # noqa: F811
    assert planner(self.cp()).stop_finish is None

  def test_other_cars_never_have_it(self, planner):  # noqa: F811
    assert self.on(planner, "toyota").stop_finish is None

  def test_the_model_asking_to_go_does_not_move_the_car(self, planner):  # noqa: F811
    p = self.held(self.on(planner))
    assert p.output_a_target == pytest.approx(STOP_FINISH_ACCEL)
    assert p.output_should_stop

  def test_it_alone_asks_to_stop(self, planner):  # noqa: F811
    # with every other candidate going, what's left is the hold
    p = self.on(planner)
    p.mpc.v_solution, p.mpc.a_solution = np.linspace(0., 10., 13), np.ones(13)
    p.a_cruise = 2.
    self.held(p)
    assert p.output_should_stop

  def test_with_the_longitudinal_off_it_learns_nothing(self, planner):  # noqa: F811
    from opendbc.car.structs import car
    p = self.on(planner)
    p.update(self.msgs(6., -1., control=car.CarControl.Actuators.LongControlState.off))
    assert not p.stop_finish.armed

  def test_without_the_option_it_would_go(self, planner):  # noqa: F811
    p = self.held(planner(self.cp()))
    assert p.output_a_target > STOP_FINISH_ACCEL + .3

  def test_above_the_stopping_speed_it_only_brakes(self, planner):  # noqa: F811
    p = self.on(planner)
    p.update(self.msgs(6., -1.))
    p.update(self.msgs(.8, -.4))
    assert p.output_a_target == pytest.approx(STOP_FINISH_ACCEL)
    assert not p.output_should_stop

  def test_the_gas_lets_go(self, planner):  # noqa: F811
    p = self.held(self.on(planner))
    p.update(self.msgs(.1, .5, gas=True))
    assert p.stop_finish is not None and not p.stop_finish.holding

  def test_the_brake_and_the_stalk_do_not_let_it_go(self, planner):  # noqa: F811
    from opendbc.car.structs import car
    p = self.held(self.on(planner))
    p.update(self.msgs(0., .5, control=car.CarControl.Actuators.LongControlState.off))
    p.update(self.msgs(0., .5))
    assert p.output_a_target == pytest.approx(STOP_FINISH_ACCEL)
    assert p.output_should_stop

  def test_moving_with_the_longitudinal_off_clears_it(self, planner):  # noqa: F811
    from opendbc.car.structs import car
    p = self.held(self.on(planner))
    p.update(self.msgs(2., .5, control=car.CarControl.Actuators.LongControlState.off))
    assert not p.stop_finish.holding

  def test_it_reads_the_car_ahead_of_us(self, planner):  # noqa: F811
    p = self.on(planner)
    msgs = self.msgs(6., -1.)
    rd = log.RadarState.new_message()
    rd.leadOne.present = True
    rd.leadOne.dRel = 8.
    rd.leadOne.modelProb = 1.
    msgs["radarState"] = rd.as_reader()
    for _ in range(round(STOP_FINISH_LEAD_CONFIRM / p.dt)):
      p.update(msgs)
    assert p.stop_finish.lead_time < STOP_FINISH_LEAD_TIME

  def published(self, p):
    from unittest.mock import MagicMock
    sm = MagicMock()
    sm.__getitem__ = lambda _, k: self.msgs(0., 0.)[k]
    sm.logMonoTime = {"modelV2": 0}
    sm.all_checks.return_value = True
    pm = MagicMock()
    p.publish(sm, pm)
    return pm.send.call_args[0][1].longitudinalPlan.stopFinishGo

  def test_the_plan_tells_selfdrived(self, planner):  # noqa: F811
    p = self.held(self.on(planner))
    assert not self.published(p)
    for _ in range(round(STOP_FINISH_GO_TIME / p.dt) + 1):
      p.update(self.msgs(.1, .5))
    assert self.published(p)

  def test_without_the_option_the_plan_says_nothing(self, planner):  # noqa: F811
    assert not self.published(planner(self.cp()))

  def test_without_the_comma_longitudinal_it_never_exists(self, planner):  # noqa: F811
    from unittest.mock import patch
    cp = SimpleNamespace(brand="gwm", openpilotLongitudinalControl=False, longitudinalActuatorDelay=0.25)
    with patch("openpilot.common.params.Params.get_bool", lambda self, k, *a, **kw: k == "StopFinish"):
      assert planner(cp).stop_finish is None
