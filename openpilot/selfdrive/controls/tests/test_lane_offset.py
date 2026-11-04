import copy
from types import SimpleNamespace

import numpy as np

from openpilot.cereal import log
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.lane_offset import (
  BLEND_TIME, LINES_LOST_TIME, LINES_STEADY_TIME, LaneOffset, MAX_OFFSET, HOLD_TIME,
  RELEASE_TIME, MAX_LEARN_LAT_ACCEL, TARGET_RATE,
)

LaneChangeState = log.LaneChangeState


def model(position, lane_width=3.6, prob=0.9, lane_change=LaneChangeState.off, curvature=0.):
  # car at `position` m right of lane center -> lane lines shifted left in the car frame
  left = SimpleNamespace(y=[-lane_width / 2 - position])
  right = SimpleNamespace(y=[lane_width / 2 - position])
  return SimpleNamespace(laneLines=[None, left, right, None], laneLineProbs=[0., prob, prob, 0.],
                         meta=SimpleNamespace(laneChangeState=lane_change),
                         action=SimpleNamespace(desiredCurvature=curvature))


def car_state(pressed=False, v_ego=15., blinker=False):
  return SimpleNamespace(steeringPressed=pressed, vEgo=v_ego, leftBlinker=blinker, rightBlinker=False)


def hold(lo, position, seconds, **kwargs):
  for _ in range(int(seconds / DT_CTRL)):
    lo.update(True, True, car_state(pressed=True, **kwargs), model(position))
  return let_go(lo, position)


def let_go(lo, position, **kwargs):
  for _ in range(int(RELEASE_TIME / DT_CTRL) + 1):
    correction = lo.update(True, True, car_state(), model(position, **kwargs))
  return correction


class TestLaneOffset:
  def test_centered_by_default(self):
    lo = LaneOffset()
    for _ in range(100):
      assert lo.update(True, True, car_state(), model(0.2)) == 0.0

  def test_learns_after_hold(self):
    lo = LaneOffset()
    hold(lo, -0.35, HOLD_TIME + 0.1)
    assert abs(lo.learned + 0.35) < 1e-6
    assert lo.offset_cm == -35

  def test_short_hold_ignored(self):
    lo = LaneOffset()
    hold(lo, -0.35, HOLD_TIME / 2)
    assert lo.learned == 0.0

  def test_curve_ignored(self):
    lo = LaneOffset()
    curve = 2 * MAX_LEARN_LAT_ACCEL / 15. ** 2
    for _ in range(int((HOLD_TIME + 0.1) / DT_CTRL)):
      lo.update(True, True, car_state(pressed=True), model(-0.35, curvature=curve))
    let_go(lo, -0.35, curvature=curve)
    assert lo.learned == 0.0

  def test_curve_during_hold_ignored(self):
    lo = LaneOffset()
    for _ in range(int((HOLD_TIME + 0.5) / DT_CTRL)):
      lo.update(True, True, car_state(pressed=True), model(-0.35))
    curve = 2 * MAX_LEARN_LAT_ACCEL / 15. ** 2
    for _ in range(100):
      lo.update(True, True, car_state(pressed=True), model(-0.35, curvature=curve))
    assert lo.learned == 0.0

  def test_short_gap_keeps_hold(self):
    lo = LaneOffset()
    for pressed_time in (HOLD_TIME * 0.6, HOLD_TIME * 0.6):
      for _ in range(int(pressed_time / DT_CTRL)):
        lo.update(True, True, car_state(pressed=True), model(-0.35))
      for _ in range(5):
        lo.update(True, True, car_state(), model(-0.35))
    assert lo.learned == 0.0, "still holding, nothing learned yet"
    let_go(lo, -0.35)
    assert abs(lo.learned + 0.35) < 1e-6

  def test_gap_does_not_add_hold_time(self):
    lo = LaneOffset()
    hold(lo, -0.35, HOLD_TIME * 0.9)
    assert lo.learned == 0.0

  def test_blinker_ignored(self):
    lo = LaneOffset()
    hold(lo, -0.35, HOLD_TIME + 0.1, blinker=True)
    assert lo.learned == 0.0

  def test_unstable_hold_ignored(self):
    lo = LaneOffset()
    for i in range(int((HOLD_TIME + 0.1) / DT_CTRL)):
      lo.update(True, True, car_state(pressed=True), model(-0.5 * i * DT_CTRL))
    lo.update(True, True, car_state(), model(-0.8))
    assert lo.learned == 0.0

  def test_back_to_center(self):
    lo = LaneOffset()
    hold(lo, -0.35, HOLD_TIME + 0.1)
    hold(lo, 0.05, HOLD_TIME + 0.1)
    assert lo.learned == 0.0

  def test_limits(self):
    lo = LaneOffset()
    hold(lo, 1.0, HOLD_TIME + 0.1)
    assert lo.learned == MAX_OFFSET
    # narrow lane keeps a margin to the line
    lo = LaneOffset()
    for _ in range(int((HOLD_TIME + 0.1) / DT_CTRL)):
      lo.update(True, True, car_state(pressed=True), model(0.45, lane_width=3.0))
    let_go(lo, 0.45, lane_width=3.0)
    assert abs(lo.learned - 0.35) < 1e-6

  def test_correction_direction(self):
    lo = LaneOffset()
    hold(lo, -0.35, LINES_STEADY_TIME + 0.1)
    # model pulls the car back to center: correction must steer left (negative curvature)
    assert lo.update(True, True, car_state(), model(-0.1)) < 0
    # car went too far left: steer right
    for _ in range(200):
      lo.target = lo.learned
      correction = lo.update(True, True, car_state(), model(-0.5))
    assert correction > 0

  def test_converges_without_jump(self):
    lo = LaneOffset()
    hold(lo, -0.35, LINES_STEADY_TIME + 0.1)
    # right after learning, the car is where the target starts: only the small feedforward that holds it there
    correction = lo.update(True, True, car_state(), model(-0.35))
    assert -5e-4 < correction < 0

  def test_reset_on_disengage_and_lane_change(self):
    lo = LaneOffset()
    hold(lo, -0.35, HOLD_TIME + 0.1)
    lo.update(False, False, car_state(), model(-0.35))
    assert lo.learned == 0.0
    hold(lo, -0.35, HOLD_TIME + 0.1)
    lo.update(True, True, car_state(), model(-0.35, lane_change=LaneChangeState.laneChangeStarting))
    assert lo.learned == 0.0

  def test_no_lanes(self):
    lo = LaneOffset()
    for _ in range(int((HOLD_TIME + 0.1) / DT_CTRL)):
      lo.update(True, True, car_state(pressed=True), model(-0.35, prob=0.1))
    lo.update(True, True, car_state(), model(-0.35, prob=0.1))
    assert lo.learned == 0.0

  def test_short_list_of_lane_lines(self):
    lo = LaneOffset()
    two_lines = SimpleNamespace(laneLines=[None, None], laneLineProbs=[0.9, 0.9], meta=model(0.).meta, action=model(0.).action)
    assert lo.update(True, True, car_state(), two_lines) == 0.0
    assert not lo.valid
    empty = model(0.)
    empty.laneLines[2] = SimpleNamespace(y=[])
    lo.update(True, True, car_state(), empty)
    assert not lo.valid
    fewer_probs = model(0.)
    fewer_probs.laneLineProbs = [0.9, 0.9]
    lo.update(True, True, car_state(), fewer_probs)
    assert not lo.valid

  def test_one_weak_line_is_enough_to_stop(self):
    for probs in ([0., 0.1, 0.9, 0.], [0., 0.9, 0.1, 0.]):
      lo = LaneOffset()
      weak = model(0.)
      weak.laneLineProbs = probs
      lo.update(True, True, car_state(), weak)
      assert not lo.valid

  def test_needs_speed(self):
    lo = LaneOffset()
    lo.update(True, True, car_state(v_ego=2.9), model(0.))
    assert not lo.valid
    lo.update(True, True, car_state(v_ego=3.1), model(0.))
    assert lo.valid

  def test_gentle_curve_still_learns(self):
    # 0.3 m/s^2 is below the limit: a hold there is a chosen position
    lo = LaneOffset()
    for _ in range(int((HOLD_TIME + 0.1) / DT_CTRL)):
      lo.update(True, True, car_state(pressed=True), model(-0.35, curvature=0.3 / 15. ** 2))
    let_go(lo, -0.35, curvature=0.3 / 15. ** 2)
    assert abs(lo.learned + 0.35) < 1e-6

  def _taught(self):
    lo = LaneOffset()
    hold(lo, -0.35, LINES_STEADY_TIME + 0.1)
    for _ in range(int(BLEND_TIME / DT_CTRL) + 10):
      correction = lo.update(True, True, car_state(), model(-0.1))
    return lo, correction

  def test_correction_fades_when_lanes_are_lost(self):
    lo, correction = self._taught()
    for _ in range(int(LINES_LOST_TIME / DT_CTRL) + 1):  # a short gap changes nothing
      faded = lo.update(True, True, car_state(), model(-0.1, prob=0.1))
    assert abs(faded) >= abs(correction) * 0.85
    for _ in range(int(BLEND_TIME / DT_CTRL) + 1):
      faded = lo.update(True, True, car_state(), model(-0.1, prob=0.1))
    assert faded == 0.

  def test_correction_fades_when_lateral_is_off(self):
    lo, correction = self._taught()
    assert abs(lo.update(True, False, car_state(), model(-0.1))) == abs(correction) * 0.98

  def test_correction_fades_while_the_driver_steers(self):
    lo, correction = self._taught()
    assert abs(lo.update(True, True, car_state(pressed=True), model(-0.1))) == abs(correction) * 0.98

  def test_correction_ends_after_going_back_to_center(self):
    lo = LaneOffset()
    hold(lo, -0.35, HOLD_TIME + 0.1)
    hold(lo, 0.05, HOLD_TIME + 0.1)
    assert lo.learned == 0.0 and lo.target != 0.0
    for _ in range(int((0.05 / TARGET_RATE + 0.5) / DT_CTRL)):
      lo.update(True, True, car_state(), model(0.05))
    assert lo.target == 0.0 and lo.update(True, True, car_state(), model(0.05)) == 0.0


class Road:
  """Straight lane with the signs and the pull of the real car, measured on rlogs of the H6 GT:
  positive curvature turns right, the model pulls back to center with PULL_POSITION and PULL_HEADING."""
  PULL_POSITION = 1.03e-3  # 1/m per m
  PULL_HEADING = 65e-3     # 1/m per rad
  LAG = 0.3                # s

  def __init__(self, position=0., v_ego=15.):
    self.position = position
    self.heading = 0.
    self.curvature = 0.
    self.v_ego = v_ego

  def step(self, correction):
    desired = -self.PULL_POSITION * self.position - self.PULL_HEADING * self.heading + correction
    self.curvature += (desired - self.curvature) * DT_CTRL / self.LAG
    self.heading += self.v_ego * self.curvature * DT_CTRL
    self.position += self.v_ego * self.heading * DT_CTRL


def drive(lo, road, seconds, **kwargs):
  positions = []
  for _ in range(int(seconds / DT_CTRL)):
    road.step(lo.update(True, True, car_state(v_ego=road.v_ego), model(road.position, **kwargs)))
    positions.append(road.position)
  return positions


class TestClosedLoop:
  def test_road_pulls_back_to_center_alone(self):
    # without a correction the car must go back, or the tests below prove nothing
    road = Road(position=0.3)
    for _ in range(int(40 / DT_CTRL)):
      road.step(0.)
    assert abs(road.position) < 0.05

  def test_keeps_taught_position_on_both_sides(self):
    for taught in (0.30, -0.30):
      for v_ego in (8., 15., 25.):
        lo = LaneOffset()
        road = Road(position=taught, v_ego=v_ego)
        hold(lo, taught, HOLD_TIME + 0.1, v_ego=v_ego)
        positions = drive(lo, road, 40.)
        side = 1 if taught > 0 else -1
        assert min(side * p for p in positions) > 0.1, "must not go through the center"
        assert max(side * p for p in positions) < 0.45, "must not run away from the target"
        assert abs(positions[-1] - taught) < 0.05

  def test_goes_back_to_center_when_taught_center(self):
    for side in (1, -1):
      lo = LaneOffset()
      road = Road(position=side * 0.3)
      hold(lo, road.position, HOLD_TIME + 0.1)
      hold(lo, side * 0.05, HOLD_TIME + 0.1)
      road.position = side * 0.05
      assert lo.learned == 0.0
      assert max(abs(p) for p in drive(lo, road, 20.)) < 0.1

  def test_narrow_lane_keeps_the_car_inside_the_limit(self):
    for side in (1, -1):
      lo = LaneOffset()
      road = Road(position=side * 0.45)
      hold(lo, road.position, HOLD_TIME + 0.1)
      assert abs(lo.learned - side * 0.45) < 1e-6
      positions = drive(lo, road, 40., lane_width=3.0)
      assert abs(positions[-1] - side * 0.35) < 0.05
      assert max(side * p for p in positions) < 0.47

  def test_never_steers_further_out_past_the_limit(self):
    # past the allowed offset and coming back fast: the predicted error points out, the correction must not
    for side in (1, -1):
      lo = LaneOffset()
      hold(lo, side * 0.3, HOLD_TIME + 0.1)
      lo.target = lo.learned = side * 0.3
      lo.rate = -side * 3.
      lo.last_position, lo.last_position_t = None, 0.
      assert lo.update(True, True, car_state(), model(side * 0.5, lane_width=2.4)) == 0.0
      assert lo.integral == 0.0

  def test_steers_back_when_past_the_limit(self):
    for side in (1, -1):
      lo = LaneOffset()
      hold(lo, side * 0.3, LINES_STEADY_TIME + 0.1)
      for _ in range(100):
        correction = lo.update(True, True, car_state(), model(side * 0.5, lane_width=2.4))
      assert side * correction < 0


class TestFlickeringLines:
  # route 2b: taught position on 81% of the time, lines flickering 5 times in 5 s, and the car wandering at 0.25 to 1 Hz
  def taught(self, position=-0.3):
    lo = LaneOffset()
    road = Road(position)
    hold(lo, position, HOLD_TIME + 1.)
    drive(lo, road, 15.)
    return lo, road

  def flicker(self, lo, road, seconds, period=.6):
    corrections, positions = [], []
    for i in range(int(seconds / DT_CTRL)):
      good = int(i * DT_CTRL / period) % 2 == 0
      c = lo.update(True, True, car_state(v_ego=road.v_ego), model(road.position, prob=.9 if good else .1))
      road.step(c)
      corrections.append(c)
      positions.append(road.position)
    return corrections, positions

  def test_the_correction_never_jumps(self):
    lo, road = self.taught()
    corrections, _ = self.flicker(lo, road, 20.)
    assert max(abs(a - b) for a, b in zip(corrections, corrections[1:], strict=False)) < 1e-4

  def test_flickering_lines_do_not_rock_the_car(self):
    lo, road = self.taught()
    self.flicker(lo, road, 30.)
    _, positions = self.flicker(lo, road, 5.)
    assert max(positions) - min(positions) < 0.005

  def test_with_lines_that_flicker_it_leaves_the_car_to_the_model(self):
    lo, road = self.taught()
    corrections, _ = self.flicker(lo, road, 20.)
    assert abs(corrections[-1]) < 1e-4

  def test_it_comes_back_once_the_lines_are_steady(self):
    lo, road = self.taught()
    self.flicker(lo, road, 10.)
    drive(lo, road, 20.)
    assert abs(road.position - -0.3) < 0.05


class TestSlowSpeedStability:
  # a rate limit on the correction weaved the car past 1 m below 20 km/h: the dose must not change the loop
  def test_no_weaving_at_low_speed(self):
    rng = np.random.default_rng(1)
    for v in (3.6, 4.2, 5., 6.):
      lo = LaneOffset()
      road = Road(-0.3, v_ego=v)
      hold(lo, -0.3, LINES_STEADY_TIME + 1., v_ego=v)
      worst = 0.
      for _ in range(int(60 / DT_CTRL)):
        seen = road.position + rng.normal(0., .017)
        road.step(lo.update(True, True, car_state(v_ego=v), model(seen)))
        worst = max(worst, abs(road.position))
      assert worst < .6


class TestSlowTraffic:
  # route 34: at 17 to 20 km/h the correction sat at its limit, 29 degrees of wheel, and pushed the car onto the one beside it
  SLOW = 20. / 3.6
  HALF = 30. / 3.6
  FULL = 40. / 3.6

  def _taught(self, position=-0.3, v_ego=60. / 3.6):
    lo = LaneOffset()
    road = Road(position, v_ego=v_ego)
    hold(lo, position, LINES_STEADY_TIME + 1., v_ego=v_ego)
    drive(lo, road, 20.)
    return lo, road

  def _slow(self, lo, road, seconds=20., **kwargs):
    road.v_ego = self.SLOW
    for _ in range(int(seconds / DT_CTRL)):
      road.step(lo.update(True, True, car_state(v_ego=road.v_ego), model(road.position, **kwargs)))

  def _climb(self, lo, road, to_kmh=40., lost=0., seconds=40.):
    # up from slow traffic in 3 s, the lines gone for the first `lost` seconds
    pos = []
    for k in range(int(seconds / DT_CTRL)):
      t = k * DT_CTRL
      road.v_ego = (20. + (to_kmh - 20.) * min(t / 3., 1.)) / 3.6
      prob = 0.1 if t < lost else 0.9
      road.step(lo.update(True, True, car_state(v_ego=road.v_ego), model(road.position, prob=prob)))
      pos.append(road.position)
    return pos

  def test_no_correction_under_25_kmh(self):
    lo, _ = self._taught()
    for position in (0., -0.1, 0.3):
      for _ in range(50):
        assert lo.update(True, True, car_state(v_ego=self.SLOW), model(position)) == 0.0

  def test_nor_while_the_driver_steers_or_the_lines_are_gone(self):
    for kwargs, pressed in (({}, True), ({"prob": 0.1}, False)):
      lo, _ = self._taught()
      for _ in range(50):
        assert lo.update(True, True, car_state(pressed=pressed, v_ego=self.SLOW), model(0., **kwargs)) == 0.0

  def test_it_fades_in_from_25_to_35_kmh(self):
    lo, _ = self._taught()
    out = {}
    for kmh in (24., 27., 30., 33., 36.):
      c = copy.deepcopy(lo)
      out[kmh] = abs(c.update(True, True, car_state(v_ego=kmh / 3.6), model(-0.2)))
    assert out[24.] == 0.0
    assert 0. < out[27.] < out[30.] < out[33.] < out[36.]

  def test_slow_the_target_follows_the_car_and_the_integral_starts_over(self):
    lo, road = self._taught()
    assert lo.integral != 0.
    self._slow(lo, road)
    assert abs(road.position + 0.3) > 0.15  # the model took the car back towards the center
    assert lo.target == lo.position and lo.integral == 0.
    assert abs(lo.learned + 0.3) < 1e-6

  def test_with_lines_gone_the_target_stays(self):
    lo, road = self._taught()
    target = lo.target
    self._slow(lo, road, prob=0.1)
    assert lo.target == target

  def test_nothing_taught_nothing_follows(self):
    lo = LaneOffset()
    road = Road(0.2, v_ego=self.SLOW)
    self._slow(lo, road)
    assert lo.target == 0.
    self._climb(lo, road)
    assert lo.correction == 0. and lo.target == 0.

  def test_taught_back_to_center_the_target_still_goes_to_the_center(self):
    lo = LaneOffset()
    hold(lo, -0.35, HOLD_TIME + 0.1)
    hold(lo, 0.05, HOLD_TIME + 0.1)
    assert lo.learned == 0. and lo.target != 0.
    for _ in range(int(5 / DT_CTRL)):
      lo.update(True, True, car_state(v_ego=self.SLOW), model(0.2))
    assert lo.target == 0.

  def test_the_integral_waits_while_the_correction_fades(self):
    lo, road = self._taught()
    self._slow(lo, road)
    road.v_ego = self.HALF  # dose 0.5: the target walks, the car is away from it, and still no integral
    for _ in range(int(5 / DT_CTRL)):
      road.step(lo.update(True, True, car_state(v_ego=road.v_ego), model(road.position)))
    assert lo.target != lo.position and lo.integral == 0.

  def test_the_position_is_kept_through_slow_traffic_and_stops(self):
    lo, road = self._taught()
    for v in (6., 3., 0., 0., 4., 6.):
      for _ in range(int(5 / DT_CTRL)):
        lo.update(True, True, car_state(v_ego=v), model(0.))
    assert abs(lo.learned + 0.3) < 1e-6
    road = Road(0., v_ego=self.FULL)
    road.position = lo.position
    drive(lo, road, 40.)
    assert abs(road.position + 0.3) < 0.03

  def test_out_of_slow_traffic_it_does_not_go_past_the_taught_position(self):
    # at 0.15 m/s and with the integral kept it went to -0.45 at 40 km/h, and -0.52 with the lines gone for 4 s
    for to_kmh in (40., 60., 90.):
      for lost in (0., 1., 4.):
        lo, road = self._taught()
        self._slow(lo, road)
        pos = self._climb(lo, road, to_kmh, lost)
        assert min(pos) > -0.38, (to_kmh, lost)
        assert abs(pos[-1] + 0.3) < 0.03, (to_kmh, lost)

  def test_back_from_lost_lines_at_speed_it_does_not_go_past(self):
    lo, road = self._taught()
    pos = []
    for k in range(int(40 / DT_CTRL)):
      prob = 0.1 if k * DT_CTRL < 4. else 0.9
      road.step(lo.update(True, True, car_state(v_ego=road.v_ego), model(road.position, prob=prob)))
      pos.append(road.position)
    assert min(pos) > -0.36 and abs(pos[-1] + 0.3) < 0.03  # it went to -0.387 before the target followed the car

  def test_at_speed_gaps_in_the_lines_do_not_lose_the_position(self):
    # starting the integral over at every gap left the car at 5 cm of the position 2% of the time, against 55%
    lo, road = self._taught(v_ego=90. / 3.6)
    pos = []
    for k in range(int(120 / DT_CTRL)):
      prob = 0.1 if (k * DT_CTRL) % 8. < 0.5 else 0.9
      road.step(lo.update(True, True, car_state(v_ego=road.v_ego), model(road.position, prob=prob)))
      pos.append(road.position)
    pos = np.array(pos)
    assert np.mean(np.abs(pos + 0.3) < 0.05) > 0.45 and abs(pos.mean() + 0.3) < 0.08

  def test_it_still_learns_in_slow_traffic(self):
    lo = LaneOffset()
    hold(lo, -0.35, HOLD_TIME + 0.1, v_ego=self.SLOW)
    assert abs(lo.learned + 0.35) < 1e-6


class TestItGoesToTheLog:
  # a curve the driver marked couldn't be told apart: the model cutting it, or the taught position. Nothing logged either
  def events(self, monkeypatch):
    seen = []
    monkeypatch.setattr("openpilot.selfdrive.controls.lib.lane_offset.cloudlog.event", lambda name, **kw: seen.append((name, kw)))
    return seen

  def test_once_a_second_with_a_position_taught(self, monkeypatch):
    seen = self.events(monkeypatch)
    lo = LaneOffset()
    hold(lo, -0.3, 3.)
    let_go(lo, -0.3)
    seen.clear()
    for _ in range(500):
      lo.update(True, True, car_state(), model(-0.3))
    assert [n for n, _ in seen] == ["lane_offset"] * 5
    assert seen[0][1]["learned_cm"] == -30 and seen[0][1]["position_cm"] == -30 and "correction" in seen[0][1]
    assert seen[0][1]["speed_dose"] == 1.

  def test_nothing_with_no_position_taught(self, monkeypatch):
    seen = self.events(monkeypatch)
    lo = LaneOffset()
    for _ in range(500):
      lo.update(True, True, car_state(), model(0.))
    assert seen == []

  def test_a_nan_from_the_model_does_not_stay(self):
    lo = LaneOffset()
    lo.update(True, True, car_state(), model(float("nan")))
    assert not lo.valid
    assert lo.update(True, True, car_state(), model(0.)) == 0.

  def test_a_bad_measure_does_not_stop_the_process(self, monkeypatch):
    seen = self.events(monkeypatch)
    lo = LaneOffset()
    hold(lo, -0.3, 3.)
    let_go(lo, -0.3)
    lo.position = float("nan")
    seen.clear()
    lo.log_t = -10.
    lo.update(True, True, car_state(), model(0., prob=0.))
    assert len(seen) == 1
