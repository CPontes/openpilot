from opendbc.car.structs import car

ButtonType = car.CarState.ButtonEvent.Type


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
