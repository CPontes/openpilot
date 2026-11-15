import numpy as np

# m, off the MPC's 6 m. Route 31 stopped at 4.7 m, the Haval's ACC at 2.8 to 3.1 m, and the owner asked for about 3 m.
# 1 m first: the real car rolls about 1.5 m past the MPC's stop and that roll was never measured with less margin (CTO review)
STOP_CLOSER_DISTANCE = 1.
STOP_CLOSER_FULL_SPEED = 5.  # m/s, all of it below this
STOP_CLOSER_MAX_SPEED = 30. / 3.6  # m/s, none of it above
STOP_CLOSER_MAX_DISTANCE = 20.  # m
# The Haval's distance (with the DBC's 3.7 m) reads more than the comma's: 3.4 m stopped, 2.6 m at 10 to 15 m, less farther
# (routes 31, 2e, 29 and others, near and below 30 km/h). It also lags while closing.
STOP_CLOSER_HAVAL_OFFSET = ([5., 15., 25.], [3.2, 2.6, .9])
STOP_CLOSER_HAVAL_NONE = 3.75  # m, with no target the field is 0 and the DBC's offset makes it 3.7
STOP_CLOSER_AGREE = (1., .1)  # m, and fraction of the distance: both have to be looking at the same car
STOP_CLOSER_CONFIRM_TIME = .5  # s of agreement to start, and of disagreement to stop: one frame either way is not enough
STOP_CLOSER_RATE_UP = 1.  # m/s, how fast it comes
STOP_CLOSER_RATE_DOWN = 2.  # m/s, and goes: the safe way is the quick one
STOP_CLOSER_MIN_SPEED = .5  # m/s, stopped it never grows: the car would creep up to the car ahead
STOP_CLOSER_HARD_BRAKE = -2.5  # m/s^2, braking harder than this it goes, and stays away until the car stops: a hard stop keeps the whole margin
STOP_CLOSER_CLOSING = (10., 3.)  # m and m/s: closing fast on a near car it goes too
STOP_CLOSER_JUMP = 2.  # m, the car ahead this much nearer in one frame is another car (a cut-in): all of it goes at once


class StopCloser:
  """How much closer than the MPC's 6 m to stop behind the car ahead, only with the comma and the Haval seeing it at the same distance.

  The owner wants about the Haval's 3 m in slow traffic (route 31). The model's distance alone can be wrong by tens of meters
  (route 31, min 73.5, a motorcycle at a red light), so the Haval's radar has to agree, and whatever the Haval sees nearer
  than the comma comes off. Slow only, never on the gas, never braking hard or closing fast, and a cut-in takes it all away.
  """
  def __init__(self, dt):
    self.dt = dt
    self.reset()

  def reset(self):
    self.extra = 0.
    self.agree_time = 0.
    self.on = False
    self.last_d = None
    self.hard_stop = False

  def _nearer(self, haval_distance, lead):
    """How much nearer the Haval sees the car than the comma does, or None when they aren't looking at the same car."""
    if not lead.present or haval_distance < STOP_CLOSER_HAVAL_NONE or lead.dRel >= STOP_CLOSER_MAX_DISTANCE:
      return None
    nearer = lead.dRel - (haval_distance - float(np.interp(lead.dRel, *STOP_CLOSER_HAVAL_OFFSET)))
    return nearer if abs(nearer) <= max(STOP_CLOSER_AGREE[0], STOP_CLOSER_AGREE[1] * lead.dRel) else None

  def update(self, v_ego, haval_distance, lead, gas_pressed, a_prev):
    """Returns the meters to take off the stopping distance."""
    d = lead.dRel if lead.present else None
    cut_in = d is not None and self.last_d is not None and self.last_d - d > STOP_CLOSER_JUMP
    self.last_d = d
    if cut_in:
      self.reset()
      self.last_d = d
      return 0.

    nearer = self._nearer(haval_distance, lead)
    step = self.dt if nearer is not None else -self.dt
    self.agree_time = float(np.clip(self.agree_time + step, 0., STOP_CLOSER_CONFIRM_TIME))
    if self.agree_time >= STOP_CLOSER_CONFIRM_TIME - 1e-6:
      self.on = True
    elif self.agree_time <= 0.:
      self.on = False

    if a_prev < STOP_CLOSER_HARD_BRAKE:
      self.hard_stop = True
    elif v_ego < STOP_CLOSER_MIN_SPEED:
      self.hard_stop = False

    closing = lead.present and lead.dRel < STOP_CLOSER_CLOSING[0] and v_ego - lead.vLead > STOP_CLOSER_CLOSING[1]
    target = 0.
    if self.on and not gas_pressed and not closing and not self.hard_stop:
      target = STOP_CLOSER_DISTANCE * float(np.interp(v_ego, [STOP_CLOSER_FULL_SPEED, STOP_CLOSER_MAX_SPEED], [1., 0.]))
      # still on but not agreeing this frame: no more than it has
      target = max(target - max(nearer, 0.), 0.) if nearer is not None else min(target, self.extra)
    if v_ego < STOP_CLOSER_MIN_SPEED:
      target = min(target, self.extra)
    self.extra += float(np.clip(target - self.extra, -STOP_CLOSER_RATE_DOWN * self.dt, STOP_CLOSER_RATE_UP * self.dt))
    return self.extra
