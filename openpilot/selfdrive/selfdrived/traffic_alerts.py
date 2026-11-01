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

AUTO_RESUME_MAX_STOP = 60.  # s, after this long the street in front may be another one. Longest stop in the logs: 62.8 s
AUTO_RESUME_REQUEST = .5  # s, it has to outlast the messages between here and the car port


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
