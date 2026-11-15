from collections import deque

import numpy as np

HAVAL_SHADOW_TIME = 0.6  # s of the Haval's distance for its closing speed
HAVAL_SHADOW_MIN_SPEED = 6.  # m/s
HAVAL_SHADOW_GAP = 7.  # m, what is left at the end, in the field's reading (it reads about 3.4 m more than the comma near)
HAVAL_SHADOW_MIN_DISTANCE = HAVAL_SHADOW_GAP + 3.  # m
HAVAL_SHADOW_MAX_DISTANCE = 45.  # m: farther, a jump to another target or a ghost in a curve came in (routes 16, 1c, 2c)
HAVAL_SHADOW_AGREE = (4., 0.25)  # m, and fraction of the distance: the comma already seeing the same car is LeadClosing's
HAVAL_SHADOW_MAX_RESIDUAL = (0.6, 0.03)  # m, and fraction of the distance: a jump is another target, or none
HAVAL_SHADOW_ACCEL = -2.  # m/s^2, what it takes to stop behind it before it counts


class HavalShadow:
  """What the Haval's distance alone would ask for, written to the log and never sent to the car.

  Route 31, min 73.5: the model put a motorcycle stopped at a red light 20 to 40 m farther than it was, and the Haval's
  radar had it right from 72 m. A rule on the Haval alone would have started about 2 s before the owner braked; on the
  routes before it, 2 right and 0 wrong in 260 min. Too few to brake on: this collects the cases, the comma's own
  braking doesn't change.
  """
  def __init__(self, dt):
    self.dt = dt
    self.size = round(HAVAL_SHADOW_TIME / dt)
    self.times = np.arange(self.size) * dt
    self.reset()

  def reset(self):
    self.distances = deque(maxlen=self.size)

  def update(self, v_ego, haval_distance, lead, pedal):
    """Returns the acceleration it would ask for, or 0. when it wouldn't."""
    self.distances.append(haval_distance)
    if len(self.distances) < self.size or pedal or v_ego < HAVAL_SHADOW_MIN_SPEED:
      return 0.
    if not (HAVAL_SHADOW_MIN_DISTANCE < haval_distance <= HAVAL_SHADOW_MAX_DISTANCE):
      return 0.
    if lead.present and abs(lead.dRel - haval_distance) <= max(HAVAL_SHADOW_AGREE[0], HAVAL_SHADOW_AGREE[1] * haval_distance):
      return 0.
    seen = np.array(self.distances)  # a frame with no target is a jump: the residual takes it
    fit = np.polyfit(self.times, seen, 1)
    if np.abs(np.polyval(fit, self.times) - seen).max() > max(HAVAL_SHADOW_MAX_RESIDUAL[0], HAVAL_SHADOW_MAX_RESIDUAL[1] * haval_distance):
      return 0.
    v_lead = max(v_ego + fit[0], 0.)
    if v_lead >= v_ego:
      return 0.
    need = -(v_ego - v_lead) ** 2 / (2. * (haval_distance - HAVAL_SHADOW_GAP))
    return float(need) if need < HAVAL_SHADOW_ACCEL else 0.
