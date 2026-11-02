from opendbc.car.structs import car
from openpilot.cereal import log
from openpilot.common.realtime import DT_CTRL

ButtonType = car.CarState.ButtonEvent.Type
EventName = log.OnroadEvent.EventName

# A pull that comes with the foot still leaving the brake isn't taken by the ACC, and commands sent with it out brought a
# Cruise Fault (route 00000028, min 7.45). Above this speed longitudinal comes back only once the ACC is driving: it took up
# to 0.8 s in the logs. Slower, it took up to 1.9 s and never faulted, and waiting would only hold the car back
RESUME_WAIT = 3.  # s
RESUME_WAIT_MIN_SPEED = 5.  # m/s


def reverse_is_parking(events, enabled: bool, brake_pressed: bool) -> None:
  """The brake no longer ends the engagement, so reverse can arrive with openpilot still on. With the brake held that's
  the driver parking: end it the way a brake press does, not with TAKE CONTROL IMMEDIATELY."""
  if enabled and brake_pressed and EventName.reverseGear in events.names:
    events.names.remove(EventName.reverseGear)
    events.add(EventName.pedalPressed)


class LongitudinalPause:
  """Always-on lateral: the brake pauses longitudinal instead of disengaging, the lever (resumeCruise) resumes it."""
  def __init__(self):
    self.paused = False
    self.wait = 0.  # s left for the ACC to take a lever pull

  def update(self, enabled: bool, brake_disengage: bool, CS) -> bool:
    """Returns True while longitudinal must be overridden (paused or brake held)."""
    if not enabled:
      self.paused = False
      self.wait = 0.
    elif brake_disengage:
      self.paused = True
      self.wait = 0.
    elif any(be.type == ButtonType.resumeCruise for be in CS.buttonEvents):
      if CS.vEgo > RESUME_WAIT_MIN_SPEED and not CS.cruiseState.driving:
        self.wait = RESUME_WAIT
      else:
        self.paused = False
        self.wait = 0.
    elif self.wait > 0.:
      if CS.cruiseState.driving:
        self.paused = False
        self.wait = 0.
      else:
        self.wait = max(self.wait - DT_CTRL, 0.)
    return self.paused or CS.brakePressed
