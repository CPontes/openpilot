from collections import deque

import numpy as np

from openpilot.common.swaglog import cloudlog

LEAD_CLOSING_TIME = 0.6  # s, of the Haval's distance. It comes at 10 Hz
LEAD_CLOSING_MAX_DISTANCE = 65.  # m, farther than this the two distances can differ by 15 m and agreeing says little
LEAD_CLOSING_MIN_SPEED = 6.  # m/s, below this the gaps are short and the one false start of 2b, at 13 km/h, goes away
LEAD_CLOSING_MIN_ERROR = 3.  # m/s, how far the model's lead speed is above the measured one
LEAD_CLOSING_MIN_PROB = 0.7
LEAD_CLOSING_MAX_RESIDUAL = (0.6, 0.03)  # m, and fraction of the distance: a jump is another target, or none (it reads 3.7 m)
LEAD_CLOSING_AGREE = (4., 0.25)  # m, and fraction of the distance: both have to be looking at the same car
LEAD_CLOSING_GAP = 7.  # m, what is left at the end
# The field (with the DBC's 3.7 m) reads more than the comma near: 3.4 m under 10 m, 2.7 m at 10 to 20 m, 0.4 m at 20 to 40 m
# (routes 31, 2e, 29); the owner's 4 m at a stop matched the comma, not the field. Taken off, in the field's own reading
LEAD_CLOSING_HAVAL_OFFSET = ([6.8, 17.7, 30.4], [3.4, 2.7, 0.4])
LEAD_CLOSING_HAVAL_NONE = 3.75  # m, with no target the field reads the DBC's 3.7
LEAD_CLOSING_START = -1.  # m/s^2, no touch on the smaller differences
LEAD_CLOSING_ACCEL_MIN = -2.  # m/s^2, only an earlier start: anything harder stays with the planner
LEAD_CLOSING_JERK = 2.5  # m/s^3
LEAD_CLOSING_CONFIRM_TIME = 0.1  # s, in a row: a third of the starts lasted one frame. Longer and route 2c gets nothing
LEAD_CLOSING_MAX_TIME = 2.  # s, of braking. If it is wrong, it can't go on
LEAD_CLOSING_REST_TIME = 1.  # s, quiet before those 2 s are back: a single frame without closing is not the end of it


class LeadClosing:
  """Starts braking when the distance to the car ahead shows it stopping before the model's lead speed does.

  The model's lead speed lags about 2 s: on route 2c it read 51 km/h with the car ahead at about 10 km/h, 47 m away, and the
  brake came at -3.7 m/s^2. The distance the Haval's own ACC measures shows that closing earlier.
  Only adds braking, only with the model looking at the same car, and never past an early, soft start.
  """
  def __init__(self, dt):
    self.dt = dt
    self.size = round(LEAD_CLOSING_TIME / dt)
    self.times = np.arange(self.size) * dt
    self.reset()

  def reset(self):
    self.distances = deque(maxlen=self.size)
    self.accel = 0.
    self.closing_time = 0.
    self.time = 0.
    self.rest_time = 0.

  def _target(self, v_ego, distance, lead):
    distance = distance - float(np.interp(distance, *LEAD_CLOSING_HAVAL_OFFSET)) if distance >= LEAD_CLOSING_HAVAL_NONE else 0.
    self.distances.append(distance)
    if len(self.distances) < self.size:
      return 0.
    if v_ego < LEAD_CLOSING_MIN_SPEED or not (LEAD_CLOSING_GAP < distance <= LEAD_CLOSING_MAX_DISTANCE):
      return 0.
    if not lead.present or lead.modelProb < LEAD_CLOSING_MIN_PROB:
      return 0.
    if abs(lead.dRel - distance) > max(LEAD_CLOSING_AGREE[0], LEAD_CLOSING_AGREE[1] * distance):
      return 0.

    seen = np.array(self.distances)
    fit = np.polyfit(self.times, seen, 1)
    if np.abs(np.polyval(fit, self.times) - seen).max() > max(LEAD_CLOSING_MAX_RESIDUAL[0], LEAD_CLOSING_MAX_RESIDUAL[1] * distance):
      return 0.

    v_lead = max(v_ego + fit[0], 0.)
    if v_lead >= v_ego or lead.vLead - v_lead < LEAD_CLOSING_MIN_ERROR:
      return 0.
    target = -(v_ego - v_lead) ** 2 / (2. * (distance - LEAD_CLOSING_GAP))
    return max(target, LEAD_CLOSING_ACCEL_MIN) if target < LEAD_CLOSING_START else 0.

  def update(self, v_ego, distance, lead, gas_pressed, a_prev):
    """Returns the acceleration this takes, or infinity when there is none."""
    target = self._target(v_ego, distance, lead)
    if gas_pressed:
      self.accel = self.closing_time = self.time = self.rest_time = 0.
      return float("inf")
    self.closing_time = self.closing_time + self.dt if target < 0. else 0.
    self.rest_time = self.rest_time + self.dt if self.closing_time == 0. and self.accel == 0. else 0.
    if self.rest_time >= LEAD_CLOSING_REST_TIME:
      self.time = 0.
    # the confirmation is for the start: a frame missing halfway through doesn't let go of the brake
    if (self.accel == 0. and self.closing_time < LEAD_CLOSING_CONFIRM_TIME - 1e-6) or self.time >= LEAD_CLOSING_MAX_TIME:
      target = 0.
    if target < 0.:
      self.time += self.dt
    if target < 0. and self.accel == 0.:
      # from where the plan already is: the model was braking at -0.9 m/s^2 by then
      self.accel = float(np.clip(a_prev, target, 0.))
      cloudlog.event("lead_closing", v_ego=round(v_ego, 1), distance=round(distance, 1), v_lead=round(lead.vLead, 1), target=round(target, 2))
    self.accel += float(np.clip(target - self.accel, -LEAD_CLOSING_JERK * self.dt, LEAD_CLOSING_JERK * self.dt))
    return self.accel if self.accel < 0. else float("inf")
