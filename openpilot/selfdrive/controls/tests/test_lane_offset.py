from types import SimpleNamespace

from openpilot.cereal import log
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.lane_offset import LaneOffset, MAX_OFFSET, HOLD_TIME, RELEASE_TIME, MAX_LEARN_LAT_ACCEL

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
    hold(lo, -0.35, HOLD_TIME + 0.1)
    # model pulls the car back to center: correction must steer left (positive curvature)
    assert lo.update(True, True, car_state(), model(-0.1)) > 0
    # car went too far left: steer right
    lo.target = lo.learned
    assert lo.update(True, True, car_state(), model(-0.5)) < 0

  def test_converges_without_jump(self):
    lo = LaneOffset()
    hold(lo, -0.35, HOLD_TIME + 0.1)
    # right after learning, the car is where the target starts: only the small feedforward that holds it there
    correction = lo.update(True, True, car_state(), model(-0.35))
    assert 0 < correction < 5e-4

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
