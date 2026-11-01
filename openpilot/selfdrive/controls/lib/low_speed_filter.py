import numpy as np

from openpilot.common.constants import CV
from openpilot.common.realtime import DT_CTRL

LOW_SPEED_FILTER_TAU = 1.5  # s
LOW_SPEED_FILTER_BP = [15. * CV.KPH_TO_MS, 30. * CV.KPH_TO_MS]  # full filter up to the first speed, none from the second


class LowSpeedCurvatureFilter:
  """Keeps the wheel still in slow traffic, moving it only when the road asks for it.

  The model's yaw rate wobbles about the same at any speed, so its curvature wobble grows as the car slows down:
  at 10 km/h, 0.03 m/s^2 is 11 degrees of wheel. Filtering the request removes that and leaves the torque available
  for real corners, which a torque cut can't do.
  """
  def __init__(self):
    self.curvature = 0.

  def update(self, curvature: float, v_ego: float, active: bool) -> float:
    tau = float(np.interp(v_ego, LOW_SPEED_FILTER_BP, [LOW_SPEED_FILTER_TAU, 0.]))
    if active and tau > 0.:
      self.curvature += DT_CTRL / (tau + DT_CTRL) * (curvature - self.curvature)
    else:
      self.curvature = curvature
    return self.curvature
