from types import SimpleNamespace

import numpy as np

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.lead_boost import LEAD_BOOST_MAX, LeadBoost
from openpilot.selfdrive.controls.tests.test_stop_point import planner  # noqa: F401

DT = 0.05


def lead(present=True, d=30., v=15.):
  return SimpleNamespace(present=present, dRel=d, vLead=v)


def run(boost, seconds, a=0.8, v=10., ahead=None, stop=False, lat=0.):
  out = a
  for _ in range(int(seconds / DT)):
    out = a + boost.update(a, stop, v, lead() if ahead is None else ahead, lat)
  return out


class TestLeadBoost:
  def setup_method(self):
    self.b = LeadBoost(DT)

  def test_adds_when_the_car_ahead_pulls_away(self):
    assert run(self.b, 2.) == 0.8 + LEAD_BOOST_MAX

  def test_it_climbs_slowly(self):
    assert 0.8 < run(self.b, .25) < 0.8 + LEAD_BOOST_MAX / 2

  def test_it_only_adds_to_a_model_that_wants_to_go(self):
    run(self.b, 2.)
    assert run(self.b, DT, a=0.) == 0.
    assert run(self.b, DT, a=-0.5) == -0.5

  def test_no_car_ahead_no_bonus(self):
    # on an empty road the model is the only one that sees the red light: 7 of 36 brake presses came right after a bonus
    assert run(self.b, 2., ahead=lead(present=False)) == 0.8

  def test_a_car_that_is_not_pulling_away_no_bonus(self):
    assert run(self.b, 2., ahead=lead(v=10.4)) == 0.8

  def test_a_car_still_close_no_bonus(self):
    assert run(self.b, 2., ahead=lead(d=14.)) == 0.8

  def test_a_model_stopping_no_bonus(self):
    assert run(self.b, 2., stop=True) == 0.8

  def test_a_curve_no_bonus(self):
    assert run(self.b, 2., lat=1.5) == 0.8

  def test_from_a_stop_the_gap_is_counted_at_a_walking_pace(self):
    assert run(self.b, 2., v=0., ahead=lead(d=1., v=5.)) == 0.8
    assert run(self.b, 2., v=0., ahead=lead(d=3., v=5.)) == 0.8 + LEAD_BOOST_MAX

  def test_it_fades_out_with_the_model(self):
    run(self.b, 2.)
    assert run(self.b, DT, a=.1) == .1 + .2
    assert run(self.b, DT, a=0.) == 0.

  def test_a_reset_forgets_it(self):
    run(self.b, 2.)
    self.b.reset()
    assert run(self.b, DT) < .8 + .05

  def test_it_leaves_faster_than_it_came(self):
    run(self.b, 2.)
    one_step = run(self.b, DT, ahead=lead(present=False))
    assert 0.8 + LEAD_BOOST_MAX - 0.11 < one_step < 0.8 + LEAD_BOOST_MAX - 0.09
    assert run(self.b, .5, ahead=lead(present=False)) == 0.8


class TestInThePlanner:
  def sm(self, boost_ok=True):
    from opendbc.car.structs import car
    cs = car.CarState.new_message()
    cs.vEgo = 10.
    cs.vCruise = 100.
    ct = log.ControlsState.new_message()
    ct.longControlState = car.CarControl.Actuators.LongControlState.pid
    rd = log.RadarState.new_message()
    rd.leadOne.present = True
    rd.leadOne.dRel = 30.
    rd.leadOne.vLead = 15. if boost_ok else 10.
    sd = log.SelfdriveState.new_message()
    sd.enabled = True
    m = log.ModelDataV2.new_message()
    m.action.desiredAcceleration = 0.5
    return {"carState": cs.as_reader(), "controlsState": ct.as_reader(), "radarState": rd.as_reader(), "selfdriveState": sd.as_reader(),
            "modelV2": m.as_reader()}

  def cp(self, brand):
    return SimpleNamespace(brand=brand, openpilotLongitudinalControl=True, longitudinalActuatorDelay=0.25)

  def run(self, p, sm, a_lead=3.):
    t = np.linspace(0., 10., 13)  # the lead plan, what the solver would answer
    p.mpc.v_solution, p.mpc.a_solution = 10. + a_lead * t, np.full(13, a_lead)
    for _ in range(80):
      p.update(sm)
    return p.output_a_target

  def test_the_plan_takes_the_bonus(self, planner):  # noqa: F811
    assert self.run(planner(self.cp("gwm")), self.sm()) > 0.8

  def test_only_with_the_car_pulling_away(self, planner):  # noqa: F811
    assert self.run(planner(self.cp("gwm")), self.sm(boost_ok=False)) < 0.6

  def test_other_cars_keep_the_model(self, planner):  # noqa: F811
    assert self.run(planner(self.cp("toyota")), self.sm()) < 0.6

  def test_the_set_speed_still_wins(self, planner):  # noqa: F811
    sm = self.sm()
    cs = sm["carState"].as_builder()
    cs.vCruise = 36.
    sm["carState"] = cs.as_reader()
    assert self.run(planner(self.cp("gwm")), sm) < 0.6

  def test_the_other_boost_is_no_model_asking(self, planner):  # noqa: F811
    p = planner(self.cp("gwm"))
    p.accel_boost.total_boost = 0.2
    sm = self.sm()
    m = sm["modelV2"].as_builder()
    m.action.desiredAcceleration = -0.15
    sm["modelV2"] = m.as_reader()
    assert self.run(p, sm) < 0.2

  def test_disengaged_it_keeps_nothing(self, planner):  # noqa: F811
    from opendbc.car.structs import car
    p = planner(self.cp("gwm"))
    self.run(p, self.sm())
    assert p.lead_boost.bonus > 0.
    sm = self.sm()
    ct = sm["controlsState"].as_builder()
    ct.longControlState = car.CarControl.Actuators.LongControlState.off
    sm["controlsState"] = ct.as_reader()
    p.update(sm)
    assert p.lead_boost.bonus == 0.

  def test_the_lead_still_wins(self, planner):  # noqa: F811
    assert self.run(planner(self.cp("gwm")), self.sm(), a_lead=.3) < 0.6
