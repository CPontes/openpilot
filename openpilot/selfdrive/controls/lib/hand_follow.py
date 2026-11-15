"""
The driver's hand leading a curve.

He pushes the wheel further into a curve than the model asks for. At low speed the gains of the
torque controller turn that small error into full torque against his hand, so the wheel jumps back
as soon as he eases off. While his hand stays on the wheel, the torque can still ease but keeps a
light tension into the curve, so the wheel follows the hand.

Lateral acceleration here is positive to the right, steering angle and torque positive to the left.
"""
import numpy as np

from openpilot.common.constants import CV
from openpilot.common.realtime import DT_CTRL

HOLD_TORQUE = 30.        # a light hand, above what the sensor reads hands off (p99 of 30 to 45 on rare peaks, so with a timer)
RELEASE_TIME = 0.3       # s, the hand must stay off this long, shorter gaps are sensor noise
MIN_ANGLE = 10.          # deg, below this it isn't a curve
MAX_SPEED = 60 * CV.KPH_TO_MS
LIGHT_TENSION = 0.5      # m/s^2, the feedforward counts as tension up to this much


class HandFollow:
  def __init__(self):
    self.active = False
    self.release = 0.

  def reset(self):
    self.active = False
    self.release = 0.

  def update(self, CS) -> bool:
    into_curve = CS.steeringTorque * CS.steeringAngleDeg > 0.
    if abs(CS.steeringAngleDeg) < MIN_ANGLE or CS.vEgo > MAX_SPEED:
      self.reset()
    elif not self.active:
      self.active = CS.steeringPressed and into_curve
    elif into_curve and abs(CS.steeringTorque) >= HOLD_TORQUE:
      self.release = 0.
    else:
      self.release += DT_CTRL
      if self.release >= RELEASE_TIME:
        self.reset()
    return self.active

  def limit(self, CS, output: float, ff: float) -> float:
    into = -np.sign(CS.steeringAngleDeg)
    tension = float(np.clip(into * ff, 0., LIGHT_TENSION))
    return float(into * max(into * output, tension))
