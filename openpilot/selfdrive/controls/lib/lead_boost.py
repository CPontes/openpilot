"""The start behind a car that pulls away. The driving model doesn't know the set speed and holds back: on route 29 the gap went
from 16 to 31 m in 12 s, and the driver marked it twice. With the car ahead pulling away, the model gets a bonus. The lead and
cruise plans still have the last word: this only stops the model from being the one that limits.

Only with a car ahead. In the replay of routes 23, 26, 28 and 29, the same bonus on an empty road came right before 7 of the 36
times the driver braked, once for a -4 m/s² stop: there the model is the only one that sees a red light. Behind a car pulling
away, it was 0 of 36, and 0 of the 113 hard brakes of the plan (tools/analise/bonus_replay.py).
"""

LEAD_BOOST_MAX = 0.5  # m/s^2
LEAD_BOOST_UP = 0.5  # m/s^3
LEAD_BOOST_DOWN = 2.0  # m/s^3
LEAD_BOOST_GAIN = 2.  # times what the model asks: none at zero, so it ends without a step
PULLING_AWAY = 0.5  # m/s faster than us
MIN_GAP = 1.45  # s, the middle following distance: a closer car comes first
MIN_GAP_SPEED = 1.5  # m/s, so a start from a stop has a gap to measure
MAX_LAT_ACCEL = 1.0  # m/s^2, not in a curve


class LeadBoost:
  def __init__(self, dt: float):
    self.dt = dt
    self.bonus = 0.

  def reset(self):
    self.bonus = 0.

  def update(self, a_model: float, model_stops: bool, v_ego: float, lead, lat_accel: float) -> float:
    """The bonus. a_model is what the model itself asks, before any other boost."""
    pulling_away = lead.present and lead.vLead > v_ego + PULLING_AWAY and lead.dRel > MIN_GAP * max(v_ego, MIN_GAP_SPEED)
    if a_model <= 0.:
      self.bonus = 0.
      return 0.
    if pulling_away and not model_stops and lat_accel < MAX_LAT_ACCEL:
      self.bonus = min(self.bonus + LEAD_BOOST_UP * self.dt, LEAD_BOOST_MAX)
    else:
      self.bonus = max(self.bonus - LEAD_BOOST_DOWN * self.dt, 0.)
    return min(self.bonus, LEAD_BOOST_GAIN * a_model)
