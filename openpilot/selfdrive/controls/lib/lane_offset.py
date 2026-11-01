"""
Driver-taught lane offset.

The driver steers the car to a new lateral position in the lane, holds it there and
lets go. That position, relative to the lane center, becomes the new target and is
kept by adding a small curvature correction on top of the model's desired curvature.
Holding the car back near the center restores the default (centered) behavior.

Frames: model laneLines are in the calibrated frame, y positive to the right.
Offsets here use the same convention: positive = car right of lane center.
"""
import numpy as np

from openpilot.cereal import log
from openpilot.common.realtime import DT_CTRL

LaneChangeState = log.LaneChangeState

HOLD_TIME = 1.5             # s, driver must hold the wheel this long before a new position is learned
RELEASE_TIME = 0.3          # s, shorter gaps in steeringPressed don't end a hold
MAX_LEARN_LAT_ACCEL = 0.4   # m/s^2, a hold in a curve is steering, not a chosen position
STABLE_WINDOW = 1.0         # s, lateral position must be stable over the end of the hold
STABLE_TOLERANCE = 0.15     # m, max lateral movement within STABLE_WINDOW
CENTER_SNAP = 0.10          # m, learned offsets smaller than this snap to center
MAX_OFFSET = 0.50           # m, max offset from lane center
MIN_LINE_MARGIN = 1.15      # m, min distance from car center to the lane line on the offset side (~half car width + 0.2 m)
MIN_LANE_PROB = 0.5         # both lane lines must be visible to learn or apply an offset
MIN_SPEED = 3.0             # m/s, no learning or correction below this speed

TARGET_RATE = 0.15          # m/s, how fast the applied target moves towards the learned one
LOOKAHEAD_TIME = 2.5        # s, pure pursuit lookahead
MIN_LOOKAHEAD = 10.0        # m
FEEDFORWARD = 0.5           # fraction of the model's pull back to center that is cancelled up front (conservative)
KI = 0.3                    # 1/s, integral gain for the remaining mismatch
DAMPING_TIME = 1.0          # s, feedback acts on the position predicted this far ahead
MAX_INTEGRAL = 0.5          # m
MAX_CURVATURE_CORRECTION = 0.01  # 1/m


class LaneOffset:
  def __init__(self):
    self.learned = 0.0       # m, offset taught by the driver
    self.target = 0.0        # m, rate limited towards learned
    self.integral = 0.0      # m
    self.position = 0.0      # m, measured car position relative to lane center
    self.left_y = 0.0
    self.right_y = 0.0
    self.valid = False
    self.hold_time = 0.0
    self.release_time = 0.0
    self.history: list[float] = []
    self.correction = 0.0    # 1/m
    self.rate = 0.0          # m/s, lateral speed relative to the lane
    self.t = 0.0
    self.last_position: float | None = None
    self.last_position_t = 0.0

  def reset(self):
    self.rate = 0.0
    self.last_position = None
    self.learned = 0.0
    self.target = 0.0
    self.integral = 0.0
    self.hold_time = 0.0
    self.release_time = 0.0
    self.history = []
    self.correction = 0.0

  def _measure(self, model_v2) -> bool:
    if len(model_v2.laneLines) < 3 or len(model_v2.laneLineProbs) < 3:
      return False
    left_y, right_y = model_v2.laneLines[1].y, model_v2.laneLines[2].y
    if not len(left_y) or not len(right_y):
      return False
    if min(model_v2.laneLineProbs[1], model_v2.laneLineProbs[2]) < MIN_LANE_PROB:
      return False
    self.left_y, self.right_y = left_y[0], right_y[0]
    self.position = -(self.left_y + self.right_y) / 2.
    return True

  def _limit(self, offset: float) -> float:
    # keep MIN_LINE_MARGIN to the lane line on the offset side, for narrow lanes
    half_width = (self.right_y - self.left_y) / 2.
    max_offset = float(np.clip(half_width - MIN_LINE_MARGIN, 0., MAX_OFFSET))
    return float(np.clip(offset, -max_offset, max_offset))

  def update(self, enabled: bool, lat_active: bool, CS, model_v2) -> float:
    """Returns the curvature correction (1/m, positive = left) to add to the desired curvature."""
    lane_changing = model_v2.meta.laneChangeState != LaneChangeState.off
    if not enabled or lane_changing:
      self.reset()
      return 0.0

    self.valid = self._measure(model_v2) and CS.vEgo > MIN_SPEED
    self.t += DT_CTRL
    if self.valid:
      # the model runs slower than this loop, only use new measurements
      if self.last_position is None:
        self.last_position, self.last_position_t = self.position, self.t
      elif self.position != self.last_position:
        raw_rate = (self.position - self.last_position) / max(self.t - self.last_position_t, DT_CTRL)
        self.rate += 0.3 * (raw_rate - self.rate)
        self.last_position, self.last_position_t = self.position, self.t
    else:
      self.last_position = None
      self.rate = 0.0
    blinker = CS.leftBlinker or CS.rightBlinker
    curving = abs(model_v2.action.desiredCurvature) * CS.vEgo ** 2 > MAX_LEARN_LAT_ACCEL
    self.release_time = 0.0 if CS.steeringPressed else self.release_time + DT_CTRL
    holding = CS.steeringPressed or (self.hold_time > 0.0 and self.release_time < RELEASE_TIME)

    # learn: driver holds the wheel, then lets go with the car steady in the lane
    if holding and self.valid and not blinker and not curving:
      if CS.steeringPressed:
        self.hold_time += DT_CTRL
      self.history.append(self.position)
      self.history = self.history[-int(STABLE_WINDOW / DT_CTRL):]
      self.integral = 0.0
    else:
      if self.hold_time >= HOLD_TIME and self.valid and not blinker and not curving and len(self.history):
        if max(self.history) - min(self.history) < STABLE_TOLERANCE:
          learned = self.position if abs(self.position) >= CENTER_SNAP else 0.0
          self.learned = self._limit(learned)
          self.target = self.position  # start from where the driver left the car, no jump
          self.integral = 0.0
      self.hold_time = 0.0
      self.history = []

    if self.learned == 0.0 and abs(self.target) < 1e-3:
      self.target = 0.0
      self.correction = 0.0
      return 0.0

    self.target += float(np.clip(self.learned - self.target, -TARGET_RATE * DT_CTRL, TARGET_RATE * DT_CTRL))

    if not (lat_active and self.valid) or CS.steeringPressed:
      # fade out the correction while lanes are not visible or the driver is steering
      self.correction *= 0.98
      return self.correction

    target = self._limit(self.target)
    error = target - self.position  # positive: target is right of the car
    self.integral = float(np.clip(self.integral + KI * error * DT_CTRL, -MAX_INTEGRAL, MAX_INTEGRAL))
    predicted_error = target - (self.position + self.rate * DAMPING_TIME)

    lookahead = max(CS.vEgo * LOOKAHEAD_TIME, MIN_LOOKAHEAD)
    # pure pursuit, positive curvature turns left:
    # - feedforward cancels the model pulling the car back to the lane center at the target offset
    # - feedback on the error to the target, integral for the mismatch between both
    correction = -2. * (FEEDFORWARD * target + predicted_error + self.integral) / lookahead ** 2

    # never push the car further out once it is past the allowed offset
    if abs(self.position) > abs(self._limit(self.position)) + 0.05:
      outward = (correction > 0) if self.position < 0 else (correction < 0)
      if outward:
        correction = 0.0
        self.integral = 0.0
    self.correction = float(np.clip(correction, -MAX_CURVATURE_CORRECTION, MAX_CURVATURE_CORRECTION))
    return self.correction

  @property
  def offset_cm(self) -> int:
    """Learned offset for display, positive = right of center."""
    return int(round(self.learned * 100))
