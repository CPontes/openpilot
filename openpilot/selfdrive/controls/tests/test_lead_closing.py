import math
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.lead_closing import (LEAD_CLOSING_ACCEL_MIN, LEAD_CLOSING_CONFIRM_TIME, LEAD_CLOSING_GAP,
                                                           LEAD_CLOSING_HAVAL_OFFSET, LEAD_CLOSING_JERK, LEAD_CLOSING_MAX_DISTANCE,
                                                           LEAD_CLOSING_MAX_TIME, LEAD_CLOSING_MIN_SPEED, LEAD_CLOSING_REST_TIME,
                                                           LEAD_CLOSING_TIME, LeadClosing)
from openpilot.selfdrive.controls.tests.test_stop_point import TestInThePlanner as Planner
from openpilot.selfdrive.controls.tests.test_stop_point import planner  # noqa: F401

DT = 0.05
START = LEAD_CLOSING_TIME + LEAD_CLOSING_CONFIRM_TIME - DT  # the frame it starts


def field(d):
  # what the Haval's field reads with the car really at d: it reads more near (lead_closing.py)
  f = d
  for _ in range(20):
    f = d + float(np.interp(f, *LEAD_CLOSING_HAVAL_OFFSET))
  return f


def lead(d, v_lead=14., present=True, prob=1.):
  return SimpleNamespace(present=present, dRel=d, vLead=v_lead, modelProb=prob)


def run(lc, seconds, v=16., v_real=2., d=50., v_lead=14., gas=False, a_prev=0., seen=None, rate=1, **kwargs):
  """The car ahead really goes at v_real, the model still reads v_lead. Ends at d. rate: frames between new distances."""
  out = math.inf
  frames = round(seconds / DT)
  now = d + frames * (v - v_real) * DT
  for i in range(frames):
    now -= (v - v_real) * DT
    held = d + (frames - 1 - (i - i % rate)) * (v - v_real) * DT
    out = lc.update(v, field(held), lead(now if seen is None else seen, v_lead, **kwargs), gas, a_prev)
  return out


def target(v, v_real, d, v_lead=14., seen=None):
  lc = LeadClosing(DT)
  run(lc, LEAD_CLOSING_TIME - DT, v=v, v_real=v_real, d=d + (v - v_real) * DT, v_lead=v_lead)
  return lc._target(v, field(d), lead(d if seen is None else seen, v_lead))


class TestTheTarget:
  def test_what_it_takes_to_reach_the_gap(self):
    assert target(16., 9., 30.) == pytest.approx(-(16. - 9.) ** 2 / (2. * (30. - LEAD_CLOSING_GAP)))

  def test_never_harder_than_the_limit(self):
    assert target(16., 2., 30.) == LEAD_CLOSING_ACCEL_MIN

  def test_a_small_braking_is_left_alone(self):
    assert target(16., 10., 30.) == 0.

  def test_the_car_ahead_never_goes_backwards(self):
    # closing faster than the car goes is noise in the distance: the most it can take is a stopped car
    assert target(8., -4., 30., v_lead=8.) == pytest.approx(-8. ** 2 / (2. * (30. - LEAD_CLOSING_GAP)))

  def test_a_car_pulling_away_is_no_closing(self):
    assert target(10., 20., 30., v_lead=30.) == 0.

  def test_at_the_gap_it_is_over(self):
    assert target(10., 2., LEAD_CLOSING_GAP) == 0.
    assert target(10., 2., LEAD_CLOSING_GAP - 1.) == 0.

  def test_too_slow(self):
    assert target(LEAD_CLOSING_MIN_SPEED - 0.1, 0., 12., v_lead=6.) == 0.
    assert target(LEAD_CLOSING_MIN_SPEED + 0.1, 0., 12., v_lead=6.) < 0.

  def test_a_jump_is_another_target(self):
    # the Haval changes to a car much closer, or loses it: that is no closing speed
    for now in (30., 3.7):
      lc = LeadClosing(DT)
      for d in [field(60.)] * 6 + [now] * 5:
        lc.update(16., d, lead(30.), False, 0.)
      assert lc._target(16., now, lead(30.)) == 0.

  def test_far_away_the_two_distances_say_little(self):
    assert target(16., 2., LEAD_CLOSING_MAX_DISTANCE + 1.) == 0.

  @pytest.mark.parametrize("d, off", [(40., 10.), (12., 4.)])  # a quarter of the distance, or 4 m when close
  def test_the_two_distances_have_to_agree(self, d, off):
    for side in (1., -1.):
      assert target(16., 4., d, seen=d + side * (off - 0.1)) < 0.
      assert target(16., 4., d, seen=d + side * (off + 0.1)) == 0.

  def test_a_small_wiggle_in_the_distance_is_fine_when_close(self):
    lc = LeadClosing(DT)
    for i in range(11, 0, -1):
      lc.update(16., field(10. + 7. * DT * i) + (0.6 if i == 6 else 0.), lead(10.), False, 0.)
    assert lc._target(16., field(10.), lead(10.)) < 0.

  def test_the_message_comes_at_half_the_rate(self):
    lc = LeadClosing(DT)
    run(lc, LEAD_CLOSING_TIME - DT, v_real=9., d=30.5, rate=2)
    seen = lc._target(16., field(30.), lead(30.))
    assert seen == pytest.approx(target(16., 9., 30.), rel=0.1)


class TestLeadClosing:
  def setup_method(self):
    self.lc = LeadClosing(DT)

  def test_nothing_until_it_has_seen_enough(self):
    assert run(self.lc, LEAD_CLOSING_TIME - DT) == math.inf

  def test_one_frame_does_not_start_it(self):
    run(self.lc, LEAD_CLOSING_TIME, d=45.)
    assert self.lc.accel == 0.

  def test_two_frames_do(self):
    assert run(self.lc, START, d=45.) < 0.

  def test_it_starts_from_where_the_plan_is(self):
    assert run(self.lc, START, d=45., a_prev=-0.9) == pytest.approx(-0.9 - LEAD_CLOSING_JERK * DT)

  def test_going_up_it_starts_from_zero(self):
    assert run(self.lc, START, d=45., a_prev=0.6) == pytest.approx(-LEAD_CLOSING_JERK * DT)

  def test_a_harder_plan_is_not_held(self):
    assert run(self.lc, START, d=45., a_prev=-3.) == pytest.approx(LEAD_CLOSING_ACCEL_MIN)

  def test_builds_up_slowly_to_the_limit(self):
    first = run(self.lc, START, d=45.)
    assert run(self.lc, DT, d=44.3) == pytest.approx(first - LEAD_CLOSING_JERK * DT)
    assert run(self.lc, 0.9, d=30.) == LEAD_CLOSING_ACCEL_MIN

  def test_it_lets_go_slowly(self):
    braking = run(self.lc, 1.2, d=45.)
    out = self.lc.update(16., field(44.), lead(44., v_lead=2.), False, 0.)
    assert out == pytest.approx(braking + LEAD_CLOSING_JERK * DT)

  def test_if_it_is_wrong_it_does_not_go_on(self):
    # what the two distances can't tell: a car stopped next to the lane, at the distance of the one being followed
    run(self.lc, LEAD_CLOSING_TIME + LEAD_CLOSING_MAX_TIME, v_real=6., d=25.)
    assert self.lc.accel == LEAD_CLOSING_ACCEL_MIN
    run(self.lc, 1., v_real=6., d=15.)
    assert self.lc.accel == 0.

  def locked(self):
    run(self.lc, LEAD_CLOSING_TIME + LEAD_CLOSING_MAX_TIME + 1., v_real=6., d=15.)
    assert self.lc.accel == 0. and self.lc.time >= LEAD_CLOSING_MAX_TIME

  def quiet(self, seconds):
    for _ in range(round(seconds / DT)):
      self.lc.update(16., field(40.), lead(40., v_lead=6.), False, 0.)

  def test_the_limit_holds_while_it_closes(self):
    self.locked()
    assert run(self.lc, 0.5, v_real=6., d=10.) == math.inf

  def test_a_short_pause_does_not_give_the_time_back(self):
    self.locked()
    self.quiet(LEAD_CLOSING_REST_TIME - 2 * DT)
    assert self.lc.time >= LEAD_CLOSING_MAX_TIME

  def test_the_time_comes_back_after_a_real_rest(self):
    self.locked()
    self.quiet(LEAD_CLOSING_REST_TIME)
    assert self.lc.time == 0.

  def test_letting_go_is_not_rest(self):
    run(self.lc, LEAD_CLOSING_TIME + 1.5, d=30.)
    self.quiet(LEAD_CLOSING_REST_TIME + 0.3)  # 0.8 s of it still letting go
    assert self.lc.time > 0.

  def test_a_frame_missing_halfway_does_not_let_go(self):
    braking = run(self.lc, 1.2, d=45.)
    self.lc.update(16., field(44.3), lead(44.3, v_lead=2.), False, 0.)
    assert self.lc.update(16., field(43.6), lead(43.6), False, 0.) == pytest.approx(braking)

  def test_reset_leaves_nothing(self):
    self.locked()
    self.lc.closing_time = self.lc.rest_time = 1.
    self.lc.reset()
    assert (self.lc.accel, self.lc.time, self.lc.closing_time, self.lc.rest_time) == (0., 0., 0., 0.)
    assert len(self.lc.distances) == 0

  def test_a_model_that_already_knows_is_left_alone(self):
    assert run(self.lc, 2., d=45., v_lead=3.) == math.inf

  @pytest.mark.parametrize("kwargs", [{"present": False}, {"prob": 0.5}, {"seen": 70.}])
  def test_only_when_all_of_it_holds(self, kwargs):
    assert run(self.lc, 2., d=45., **kwargs) == math.inf

  def test_no_target_from_the_haval(self):
    for _ in range(40):
      assert self.lc.update(16., 3.7, lead(30.), False, 0.) == math.inf

  def test_the_gas_ends_it(self):
    run(self.lc, 1.2, d=45.)
    self.lc.rest_time = 0.5
    assert run(self.lc, DT, d=44., gas=True) == math.inf
    assert (self.lc.accel, self.lc.time, self.lc.closing_time, self.lc.rest_time) == (0., 0., 0., 0.)


class TestInThePlanner(Planner):
  def closing(self, sm, d, gas=False):
    from opendbc.car.structs import car
    cs = car.CarState.new_message()
    cs.vEgo, cs.vCruise, cs.camLeadDistance, cs.gasPressed = 16., 90., d, gas
    return sm | {"carState": cs.as_reader(), "radarState": self.radar(d).as_reader()}

  def radar(self, d):
    from openpilot.cereal import log
    rd = log.RadarState.new_message()
    rd.leadOne.present, rd.leadOne.dRel, rd.leadOne.vLead, rd.leadOne.modelProb = True, d, 14., 1.
    return rd

  def on(self, planner, brand="gwm"):  # noqa: F811
    Params().put_bool("LeadClosing", True)
    try:
      return planner(self.cp(brand))
    finally:
      Params().remove("LeadClosing")

  def gone(self, p, sm, frames=24):
    d = 60.
    for _ in range(frames):
      p.mpc.v_solution = p.mpc.v_solution * 0. + 16.
      d -= 14. * DT
      p.update(self.closing(sm, d))
    return p

  def long_off(self, sm):
    from opendbc.car.structs import car
    from openpilot.cereal import log
    ct = log.ControlsState.new_message()
    ct.longControlState = car.CarControl.Actuators.LongControlState.off
    return sm | {"controlsState": ct.as_reader()}

  def test_off_by_default(self, planner):  # noqa: F811
    assert planner(self.cp("gwm")).lead_closing is None

  def test_it_takes_part_in_the_plan_when_on(self, planner):  # noqa: F811
    p = self.gone(self.on(planner), self.sm())
    assert p.lead_closing.accel < -1.
    assert p.output_a_target <= p.lead_closing.accel

  def test_it_is_given_the_last_plan(self, planner, monkeypatch):  # noqa: F811
    p = self.on(planner)
    seen, last = [], []
    real = p.lead_closing.update

    def spy(v_ego, distance, lead, gas_pressed, a_prev):
      seen.append(a_prev)
      last.append(p.output_a_target)
      return real(v_ego, distance, lead, gas_pressed, a_prev)
    monkeypatch.setattr(p.lead_closing, "update", spy)
    self.gone(p, self.sm())
    assert seen == last and min(seen) < -1.

  def test_it_is_told_about_the_gas(self, planner):  # noqa: F811
    p = self.gone(self.on(planner), self.sm())
    p.update(self.closing(self.sm(), 26., gas=True))
    assert p.lead_closing.accel == 0.

  def test_it_never_asks_to_stop(self, planner):  # noqa: F811
    assert not self.gone(self.on(planner), self.sm()).output_should_stop

  def test_the_end_of_the_longitudinal_clears_it(self, planner):  # noqa: F811
    p = self.gone(self.on(planner), self.sm())
    p.update(self.closing(self.long_off(self.sm()), 30.))
    assert p.lead_closing.accel == 0.
    assert len(p.lead_closing.distances) == 0

  def test_with_the_longitudinal_off_it_learns_nothing(self, planner):  # noqa: F811
    p = self.on(planner)
    self.gone(p, self.long_off(self.sm()))
    assert len(p.lead_closing.distances) == 0

  def test_other_cars_never_have_it(self, planner):  # noqa: F811
    assert self.on(planner, "toyota").lead_closing is None


class TestTheHavalReading:
  # the field reads 3.4 m more than the comma near, 2.7 m at 10 to 20 m and 0.4 m at 20 to 40 m (routes 31, 2e, 29)
  def test_near_it_comes_off(self):
    lc = LeadClosing(DT)
    for _ in range(12):
      lc._target(16., 7.4, lead(4.))
    assert lc.distances[-1] == pytest.approx(4., abs=.1)

  def test_farther_less_of_it(self):
    lc = LeadClosing(DT)
    lc._target(16., 30.4, lead(30.))
    assert lc.distances[-1] == pytest.approx(30., abs=.1)

  def test_no_target_is_none(self):
    lc = LeadClosing(DT)
    lc._target(16., 3.7, lead(30.))
    assert lc.distances[-1] == 0.

  def test_the_comma_and_the_haval_on_the_same_car_near(self):
    # the comma at 10 m, the field at 13 m: the same car. Read as it came, 3 m off counted as only just agreeing
    assert target(16., 4., 10.) < 0.
