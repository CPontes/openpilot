import numpy as np

from openpilot.common.realtime import DT_CTRL

LEAD_SETTLE_TIME = .5  # s, the distance is still changing right after stopping
LEAD_BASE_TIME = 2.  # s, stopped this long before the distance becomes the reference
LEAD_BASE_PRESENT = .8  # share of that time with a lead
LEAD_MAX_DISTANCE = 25.  # m, farther than this it isn't the car we stopped behind
LEAD_DEPART_DISTANCE = 3.  # m, over the vision distance noise at a stop: 2.3 m at p90, 92 stops
LEAD_DEPART_SPEED = .5  # m/s
LEAD_DEPART_TIME = .5  # s


class LeadDepartAlert:
  """Warns, once per stop, that the car ahead pulled away. It only warns: it never moves the car."""
  def __init__(self, dt: float = DT_CTRL):
    self.dt = dt
    self.reset()

  def reset(self) -> None:
    self.stopped_frames = 0
    self.samples: list[float] = []
    self.base: float | None = None
    self.depart_frames = 0
    self.done = False

  def update(self, standstill: bool, in_drive: bool, gas_pressed: bool, lead_present: bool, d_rel: float, v_lead: float) -> bool:
    if not standstill or not in_drive:
      self.reset()
      return False
    if self.done:
      return False

    self.stopped_frames += 1
    stopped_time = self.stopped_frames * self.dt
    if self.base is None:
      if stopped_time >= LEAD_SETTLE_TIME and lead_present:
        self.samples.append(d_rel)
      if stopped_time >= LEAD_BASE_TIME:
        expected = (LEAD_BASE_TIME - LEAD_SETTLE_TIME) / self.dt
        if len(self.samples) >= LEAD_BASE_PRESENT * expected and np.median(self.samples) < LEAD_MAX_DISTANCE:
          self.base = float(np.median(self.samples))
        else:
          self.done = True  # stopped with no car ahead: nothing to watch
      return False

    departing = lead_present and not gas_pressed and d_rel - self.base >= LEAD_DEPART_DISTANCE and v_lead >= LEAD_DEPART_SPEED
    self.depart_frames = self.depart_frames + 1 if departing else 0
    if self.depart_frames * self.dt >= LEAD_DEPART_TIME - 1e-6:
      self.done = True
      return True
    return False
