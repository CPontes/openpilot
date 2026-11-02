from opendbc.car.structs import car
from openpilot.cereal import log

ButtonType = car.CarState.ButtonEvent.Type
EventName = log.OnroadEvent.EventName


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

  def update(self, enabled: bool, brake_disengage: bool, CS) -> bool:
    """Returns True while longitudinal must be overridden (paused or brake held)."""
    if not enabled:
      self.paused = False
    elif brake_disengage:
      self.paused = True
    elif any(be.type == ButtonType.resumeCruise for be in CS.buttonEvents):
      self.paused = False
    return self.paused or CS.brakePressed
