import numpy as np

STOP_CLOSER_DISTANCE = 1.5  # m, off the MPC's 6 m: about 3 m at a stop, like the Haval's ACC (route 31: 4.7 m, Haval 2.8 to 3.1 m)
STOP_CLOSER_FULL_SPEED = 5.  # m/s, all of it below this
STOP_CLOSER_MAX_SPEED = 30. / 3.6  # m/s, none of it above
STOP_CLOSER_MAX_DISTANCE = 20.  # m
# The Haval's distance (with the DBC's 3.7 m) reads more than the comma's: 3.4 m stopped, 2.6 m at 10 to 15 m, less farther
# (routes 31, 2e, 29 and others, near and below 30 km/h). It also lags while closing.
STOP_CLOSER_HAVAL_OFFSET = ([5., 15., 25.], [3.2, 2.6, .9])
STOP_CLOSER_AGREE = (1.5, .15)  # m, and fraction of the distance: both have to be looking at the same car
STOP_CLOSER_CONFIRM_TIME = .5  # s of agreement to start, and of disagreement to stop: one frame either way is not enough
STOP_CLOSER_RATE_UP = 1.  # m/s, how fast it comes
STOP_CLOSER_RATE_DOWN = 2.  # m/s, and goes: the safe way is the quick one
STOP_CLOSER_MIN_SPEED = .5  # m/s, stopped it never grows: the car would creep up to the car ahead
STOP_CLOSER_HARD_BRAKE = -2.  # m/s^2, braking harder than this it never grows: a hard stop keeps the whole margin


class StopCloser:
  """How much closer than the MPC's 6 m to stop behind the car ahead, only with the comma and the Haval seeing it at the same distance.

  The owner wants about the Haval's 3 m in slow traffic (route 31). The model's distance alone can be wrong by tens of meters
  (route 31, min 73.5, a motorcycle at a red light), so the Haval's radar has to agree. Slow only, and never on the gas.
  """
  def __init__(self, dt):
    self.dt = dt
    self.reset()

  def reset(self):
    self.extra = 0.
    self.agree_time = 0.
    self.on = False

  def _agree(self, haval_distance, lead):
    if not lead.present or haval_distance <= 0. or lead.dRel >= STOP_CLOSER_MAX_DISTANCE:
      return False
    offset = float(np.interp(lead.dRel, *STOP_CLOSER_HAVAL_OFFSET))
    return abs(haval_distance - offset - lead.dRel) <= max(STOP_CLOSER_AGREE[0], STOP_CLOSER_AGREE[1] * lead.dRel)

  def update(self, v_ego, haval_distance, lead, gas_pressed, a_prev):
    """Returns the meters to take off the stopping distance."""
    step = self.dt if self._agree(haval_distance, lead) else -self.dt
    self.agree_time = float(np.clip(self.agree_time + step, 0., STOP_CLOSER_CONFIRM_TIME))
    if self.agree_time >= STOP_CLOSER_CONFIRM_TIME - 1e-6:
      self.on = True
    elif self.agree_time <= 0.:
      self.on = False

    target = 0.
    if self.on and not gas_pressed:
      target = STOP_CLOSER_DISTANCE * float(np.interp(v_ego, [STOP_CLOSER_FULL_SPEED, STOP_CLOSER_MAX_SPEED], [1., 0.]))
    if v_ego < STOP_CLOSER_MIN_SPEED or a_prev < STOP_CLOSER_HARD_BRAKE:
      target = min(target, self.extra)
    self.extra += float(np.clip(target - self.extra, -STOP_CLOSER_RATE_DOWN * self.dt, STOP_CLOSER_RATE_UP * self.dt))
    return self.extra
