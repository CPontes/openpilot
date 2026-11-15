import numpy as np

STOP_POINT_START_SPEED = (3., 12.5)  # m/s, the band of the 4.8 h of logs where this was checked
STOP_POINT_ACTION = -0.3  # m/s^2, it only starts with the model already braking
STOP_POINT_END_SPEED = 0.6  # m/s, at the end of the model's plan
STOP_POINT_START_TIME = 0.5  # s
STOP_POINT_MARGIN = 1.  # m, short of the model's point
STOP_POINT_MIN_DISTANCE = 2.  # m, so the very end of the stop isn't a jolt
STOP_POINT_ACCEL_MIN = -1.5  # m/s^2
STOP_POINT_JERK = 1.  # m/s^3


class StopPoint:
  """Brakes for the point where the model plans to stop.

  The plan ends in the right place, but what the model asks for right now falls short of it: with no car ahead, the only stop it
  made on its own ended 5 m past the plan, on the crosswalk. The driver goes from 10 km/h to a stop in 3.3 m, this took 6 m.
  Only adds braking, and only with the model already braking: in 4.8 h of logs it started 3 times, all before real stops.
  """
  def __init__(self, dt):
    self.dt = dt
    self.reset()

  def reset(self):
    self.time = 0.
    self.accel = 0.

  def update(self, v_ego, lead_present, gas_pressed, model_v2):
    """Returns the acceleration this point takes, or infinity when there is none."""
    x, vx = model_v2.position.x, model_v2.velocity.x
    action = model_v2.action.desiredAcceleration
    started = self.time >= STOP_POINT_START_TIME
    plan_stops = len(x) > 0 and vx[-1] < STOP_POINT_END_SPEED and not lead_present and not gas_pressed
    in_band = STOP_POINT_START_SPEED[0] < v_ego < STOP_POINT_START_SPEED[1] and action < STOP_POINT_ACTION
    if not plan_stops or not (in_band or (started and action < 0.)):
      self.reset()
      return float('inf')

    self.time += self.dt
    if self.time < STOP_POINT_START_TIME:
      self.accel = action
      return float('inf')

    target = -v_ego ** 2 / (2. * max(x[-1] - STOP_POINT_MARGIN, STOP_POINT_MIN_DISTANCE))
    target = float(np.clip(target, STOP_POINT_ACCEL_MIN, 0.))
    self.accel = max(target, self.accel - STOP_POINT_JERK * self.dt)
    return self.accel
