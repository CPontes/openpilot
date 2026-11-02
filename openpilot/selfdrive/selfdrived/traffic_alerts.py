import numpy as np

from openpilot.cereal import log
from openpilot.common.realtime import DT_CTRL

LEAD_SETTLE_TIME = .5  # s, the distance is still changing right after stopping
LEAD_BASE_TIME = 2.  # s, stopped this long before the distance becomes the reference
LEAD_BASE_PRESENT = .8  # share of that time with a lead
LEAD_MAX_DISTANCE = 25.  # m, farther than this it isn't the car we stopped behind
LEAD_DEPART_DISTANCE = 3.  # m, over the vision distance noise at a stop: 2.3 m at p90, 92 stops
LEAD_DEPART_SPEED = .5  # m/s
LEAD_DEPART_TIME = .5  # s

AUTO_RESUME_MAX_STOP = 180.  # s, a 2 min red light plus the queue ahead. Longest engaged stop in the logs: 93 s, the ACC still waiting
AUTO_RESUME_REQUEST = .5  # s, it has to outlast the messages between here and the car port

BUMP_MIN_SPEED = 30.  # km/h, the ACC is never set lower. 12 routes
BUMP_MAX_SPEED = 90.  # km/h, faster than this the point is probably wrong
BUMP_MAX_DROP = 30.  # km/h, 6 presses: what the panda allows in a minute
BUMP_MAX_TIME = 60.  # s
BUMP_NO_RESPONSE = 2.5  # s, about 5 presses that didn't move the set speed

ACC_LOST_DRIVEN = 3.  # s, the ACC holding the speed before this counts as losing it
ACC_LOST_TIME = .5  # s, a short blip of the ACC state is not a drop
ACC_LOST_PEDAL_HOLD = 2.  # s, the ACC stepping aside for a pedal is the driver's doing
ACC_LOST_MIN_SPEED = 8.  # m/s, in slow traffic the ACC stops and waits on its own


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


def driver_attentive(dm) -> bool:
  """The camera sees the driver looking at the road, with no alert going. Wheel touch mode says nothing about that."""
  vision = dm.visionPolicyState
  return bool(dm.activePolicy == log.DriverMonitoringState.MonitoringPolicy.vision and dm.alertLevel == log.DriverMonitoringState.AlertLevel.none and
              vision.faceDetected and not vision.isDistracted)


class BumpSlowdown:
  """Takes the ACC down to the speed set for a bump, a press at a time. It only goes down: the driver raises it again.

  Anything unusual ends it for this bump, and the warning stays. Below BUMP_MIN_SPEED it never starts: the ACC can't go there.
  """
  def __init__(self, dt: float = DT_CTRL):
    self.dt = dt
    self.reset()

  def reset(self) -> None:
    self.target: float | None = None
    self.set_prev: float | None = None
    self.frames = 0
    self.stuck_frames = 0

  def start(self, target: float, speed: float) -> bool:
    if self.target is not None or target < BUMP_MIN_SPEED or speed > BUMP_MAX_SPEED:
      return False
    self.target = float(target)
    self.frames = round(BUMP_MAX_TIME / self.dt)
    return True

  def update(self, enabled: bool, set_speed: float, gas_pressed: bool, brake_pressed: bool) -> bool:
    if self.target is None:
      return False
    self.frames -= 1
    if self.set_prev is None:
      self.target = max(self.target, set_speed - BUMP_MAX_DROP)
    else:
      self.stuck_frames = 0 if set_speed < self.set_prev else self.stuck_frames + 1
    raised = self.set_prev is not None and set_speed > self.set_prev
    stuck = self.stuck_frames * self.dt >= BUMP_NO_RESPONSE
    if not enabled or gas_pressed or brake_pressed or raised or stuck or self.frames <= 0 or set_speed <= self.target:
      self.reset()
      return False
    self.set_prev = set_speed
    return True


class StopHoldAlert:
  """Once per hold: the plan keeps the flag on until the gas."""
  def __init__(self):
    self.prev = False

  def update(self, go_asked):
    rising = go_asked and not self.prev
    self.prev = go_asked
    return rising


class AutoResume:
  """Asks the ACC to pull away, once per stop, after the car ahead left. Anything unusual leaves the stop to the driver."""
  def __init__(self, dt: float = DT_CTRL):
    self.dt = dt
    self.depart = LeadDepartAlert(dt)
    self.reset()

  def reset(self) -> None:
    self.stopped_frames = 0
    self.driver_took_over = False
    self.request_frames = 0

  def update(self, enabled: bool, standstill: bool, in_drive: bool, gas_pressed: bool, brake_pressed: bool,
             lead_present: bool, d_rel: float, v_lead: float, attentive: bool) -> bool:
    departed = self.depart.update(standstill, in_drive, gas_pressed, lead_present, d_rel, v_lead)
    if not standstill or not in_drive:
      self.reset()
      return False

    self.stopped_frames += 1
    self.driver_took_over |= gas_pressed or brake_pressed or not enabled
    if self.driver_took_over:
      self.request_frames = 0
      return False
    if self.request_frames > 0:
      self.request_frames -= 1
      return True
    if departed and attentive and self.stopped_frames * self.dt <= AUTO_RESUME_MAX_STOP:
      self.request_frames = round(AUTO_RESUME_REQUEST / self.dt) - 1
      return True
    return False


class AccLostAlert:
  """Warns, once per drop, that the Haval ACC left while openpilot steers: nobody asked and the car starts to slow down.

  It only warns. Cancel disengages openpilot, so what's left to rule out is a pedal.
  """
  def __init__(self, dt: float = DT_CTRL):
    self.dt = dt
    self.pedal_frames = 0
    self.reset()

  def reset(self) -> None:
    self.driving_frames = 0
    self.lost_frames = 0

  def update(self, enabled: bool, acc_driving: bool, v_ego: float, gas_pressed: bool, brake_pressed: bool) -> bool:
    self.pedal_frames = round(ACC_LOST_PEDAL_HOLD / self.dt) if gas_pressed or brake_pressed else max(self.pedal_frames - 1, 0)
    if not enabled:
      self.reset()
      return False
    if acc_driving:
      self.driving_frames += 1
      self.lost_frames = 0
      return False
    if self.driving_frames * self.dt < ACC_LOST_DRIVEN or self.pedal_frames > 0 or v_ego < ACC_LOST_MIN_SPEED:
      self.reset()
      return False

    self.lost_frames += 1
    if self.lost_frames * self.dt >= ACC_LOST_TIME - 1e-6:
      self.reset()
      return True
    return False
