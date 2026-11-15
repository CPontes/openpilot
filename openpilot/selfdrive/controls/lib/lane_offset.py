"""
Driver-taught lane offset.

The driver steers the car to a new lateral position in the lane, holds it there and
lets go. That position, relative to the lane center, becomes the new target and is
kept by adding a small curvature correction on top of the model's desired curvature.
Holding the car back near the center restores the default (centered) behavior.

Frames: model laneLines are in the calibrated frame, y positive to the right.
Offsets here use the same convention: positive = car right of lane center.
Curvature follows the model too: positive turns right. Both measured on the road
against GPS and the steering wheel, not assumed.
"""
import numpy as np

from openpilot.cereal import log
from openpilot.common.realtime import DT_CTRL
from openpilot.common.swaglog import cloudlog

LaneChangeState = log.LaneChangeState

HOLD_TIME = 1.5             # s, driver must hold the wheel this long before a new position is learned
RELEASE_TIME = 0.3          # s, shorter gaps in steeringPressed don't end a hold
LOG_PERIOD = 1.0            # s, while a position is taught
LINES_STEADY_TIME = 2.0     # s, lane lines visible this long before correcting. Flickering lines rocked the car at 0.25 to 1 Hz (route 2b)
LINES_LOST_TIME = 0.25      # s, shorter gaps don't count: most gaps are this short
BLEND_TIME = 2.0            # s, from none of the correction to all of it. A dose, never a limit on it: a rate limit in this loop weaves the car
MAX_LEARN_LAT_ACCEL = 0.4   # m/s^2, a hold in a curve is steering, not a chosen position
STABLE_WINDOW = 1.0         # s, lateral position must be stable over the end of the hold
STABLE_TOLERANCE = 0.15     # m, max lateral movement within STABLE_WINDOW
CENTER_SNAP = 0.10          # m, learned offsets smaller than this snap to center
MAX_OFFSET = 0.50           # m, max offset from lane center
MIN_LINE_MARGIN = 1.15      # m, min distance from car center to the lane line on the offset side (~half car width + 0.2 m)
MIN_LANE_PROB = 0.5         # both lane lines must be visible to learn or apply an offset
MIN_SPEED = 3.0             # m/s, no learning or correction below this speed
# The correction fades in from CORRECTION_MIN_SPEED and is all there from CORRECTION_FULL_SPEED; the learned position stays.
# Slow, the lines flicker and the pure pursuit looks only 10 m ahead: 20 cm of reading noise was 12 degrees of wheel, and at
# 17 to 20 km/h the correction sat at its limit, 29 degrees of wheel, pushing the car onto the one beside it (route 34, 14.70
# to 22.88 min). The owner chose this over keeping a weaker correction in slow traffic (04/11)
CORRECTION_MIN_SPEED = 25. / 3.6  # m/s
CORRECTION_FULL_SPEED = 35. / 3.6  # m/s

# m/s, how fast the applied target moves towards the learned one. Coming back to it from where the model left the car, this
# loop goes past it: by 6 cm typically and up to 27 to 32 cm in 27 returns of routes 31 and 34 (route 34, 57.9 to 58.1 min),
# and 15 to 22 cm on the road of the tests at 40 km/h at 0.15 m/s. At this pace, with the integral starting over, 7 cm there
TARGET_RATE = 0.03
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
    self.log_t = 0.0
    self.lines_time = 0.0    # s, how long the lane lines have been visible
    self.lost_time = 0.0     # s, how long they have been gone
    self.feedback = 0.0      # 1/m, the correction before the dose
    self.blend = 0.0         # how much of it goes out

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
    self.feedback = 0.0
    self.blend = 0.0

  def _measure(self, model_v2) -> bool:
    if len(model_v2.laneLines) < 3 or len(model_v2.laneLineProbs) < 3:
      return False
    left_y, right_y = model_v2.laneLines[1].y, model_v2.laneLines[2].y
    if not len(left_y) or not len(right_y):
      return False
    if min(model_v2.laneLineProbs[1], model_v2.laneLineProbs[2]) < MIN_LANE_PROB:
      return False
    if not (np.isfinite(left_y[0]) and np.isfinite(right_y[0])):
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
    """Returns the curvature correction (1/m, positive = right) to add to the desired curvature."""
    lane_changing = model_v2.meta.laneChangeState != LaneChangeState.off
    if not enabled or lane_changing:
      self.reset()
      return 0.0

    self.valid = self._measure(model_v2) and CS.vEgo > MIN_SPEED
    speed_dose = float(np.clip((CS.vEgo - CORRECTION_MIN_SPEED) / (CORRECTION_FULL_SPEED - CORRECTION_MIN_SPEED), 0., 1.))
    self.t += DT_CTRL
    if self.learned != 0.0 and self.t - self.log_t >= LOG_PERIOD:
      self.log_t = self.t
      cloudlog.event("lane_offset", learned_cm=self.offset_cm, position_cm=round(self.position * 100, 1), correction=round(self.correction, 5),
                     speed_dose=round(speed_dose, 2))
    self.lost_time = 0.0 if self.valid else self.lost_time + DT_CTRL
    if self.lost_time > LINES_LOST_TIME:
      self.lines_time = 0.0
    elif self.valid:
      self.lines_time += DT_CTRL
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
      self.feedback = 0.0
      self.blend = 0.0
      return 0.0

    self.target += float(np.clip(self.learned - self.target, -TARGET_RATE * DT_CTRL, TARGET_RATE * DT_CTRL))
    if (speed_dose == 0. or self.blend == 0.) and self.valid and self.learned != 0.:
      # slow, or with the lines gone long enough, the car goes where the model puts it: once the correction comes back it
      # moves to the taught position from there, no jump
      self.target = self.position
      if speed_dose == 0.:
        # after slow traffic the integral starts over, as it would push for the old place. Not after a gap in the lines: at
        # speed it holds part of the position, and gaps of 0.5 s every 8 s left the car at 5 cm of it 2% of the time, not 55%
        self.integral = 0.

    if not lat_active or CS.steeringPressed:
      # fade out the correction while the driver is steering
      self.blend *= 0.98
      self.correction = self.feedback * self.blend * speed_dose
      return self.correction
    # no lanes, or lanes that come and go: slowly back to what the model does, not on and off
    steady = self.lines_time >= LINES_STEADY_TIME
    self.blend = float(np.clip(self.blend + (DT_CTRL if steady else -DT_CTRL) / BLEND_TIME, 0., 1.))
    if not self.valid:
      self.correction = self.feedback * self.blend * speed_dose
      return self.correction

    target = self._limit(self.target)
    error = target - self.position  # positive: target is right of the car
    # only with all of the correction going out: integrating with it faded winds up, and pushes once back up to speed
    if steady and speed_dose == 1.:
      self.integral = float(np.clip(self.integral + KI * error * DT_CTRL, -MAX_INTEGRAL, MAX_INTEGRAL))
    predicted_error = target - (self.position + self.rate * DAMPING_TIME)

    lookahead = max(CS.vEgo * LOOKAHEAD_TIME, MIN_LOOKAHEAD)
    # pure pursuit, positive curvature turns right:
    # - feedforward cancels the model pulling the car back to the lane center at the target offset
    # - feedback on the error to the target, integral for the mismatch between both
    correction = 2. * (FEEDFORWARD * target + predicted_error + self.integral) / lookahead ** 2

    # never push the car further out once it is past the allowed offset
    if abs(self.position) > abs(self._limit(self.position)) + 0.05:
      outward = (correction < 0) if self.position < 0 else (correction > 0)
      if outward:
        correction = 0.0
        self.integral = 0.0
    self.feedback = float(np.clip(correction, -MAX_CURVATURE_CORRECTION, MAX_CURVATURE_CORRECTION))
    self.correction = self.feedback * self.blend * speed_dose
    return self.correction

  @property
  def offset_cm(self) -> int:
    """Learned offset for display, positive = right of center."""
    return int(round(self.learned * 100))
