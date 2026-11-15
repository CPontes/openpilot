STOP_FINISH_ARM_SPEED = 4.2  # m/s, 15 km/h: a stop the model came down to, not a crawl
STOP_FINISH_ARM_ACTION = -0.3  # m/s^2, it only arms with the model already braking
STOP_FINISH_END_SPEED = 0.6  # m/s, at the end of the model's plan
STOP_FINISH_RISE_TIME = 1.  # s of the model asking for more, it slowed down and went on: a bump or a curve
STOP_FINISH_SPEED = 0.85  # m/s, 3 km/h: from here on the stop is finished
STOP_FINISH_ACCEL = -0.6  # m/s^2, what the model was asking for when it changed its mind
STOP_FINISH_LEAD_DISTANCE = 15.  # m, a car this close is what we're stopping for. Farther out, at a stop, the leads are ghosts of p 0.5 to 0.7
STOP_FINISH_LEAD_CONFIRM = .5  # s, ghosts and cross traffic get this close too, for a moment
STOP_FINISH_LEAD_TIME = 2.  # s, a car that blinks out for a moment isn't gone
STOP_FINISH_GO_ACTION = 0.3  # m/s^2, the model asking to go during the hold
STOP_FINISH_GO_TIME = 1.  # s


class StopFinish:
  """Finishes a stop the model started with no car ahead, and stays there until the driver taps the gas.

  The model doesn't read the light reliably. With nothing ahead it brought the car from 30 to 1.3 km/h at a red light, past the
  crosswalk, and asked for +0.55 m/s^2 into the cross traffic. Standing still it would be free to do the same, so the hold goes on
  until the gas, the same rule as a green light with no car ahead: the driver says go.
  """
  def __init__(self, dt):
    self.dt = dt
    self.close_time = 0.
    self.lead_time = STOP_FINISH_LEAD_TIME
    self.reset()

  def reset(self):
    self.armed = False
    self.holding = False
    self.rise_time = 0.
    self.go_time = 0.
    self.go_asked = False  # stays until the gas, or a model that hesitates asks over and over

  def long_off(self, v_ego, gas_pressed):
    """A touch of the brake drops the longitudinal, not the stop: with the stalk after it the car would leave on the model's word."""
    holding = self.holding and v_ego < STOP_FINISH_SPEED and not gas_pressed
    self.reset()
    self.holding = holding

  def update(self, v_ego, lead, gas_pressed, model_v2):
    """Returns the acceleration of the hold, or infinity when there is none."""
    close = lead.present and lead.dRel < STOP_FINISH_LEAD_DISTANCE
    self.close_time = self.close_time + self.dt if close else 0.
    if self.close_time >= STOP_FINISH_LEAD_CONFIRM - 1e-6:
      self.lead_time = 0.
      self.armed = False  # the lead logic has it, even if the car ahead leaves before we stop
    else:
      self.lead_time += self.dt

    if gas_pressed:
      self.reset()
      return float('inf')

    action = model_v2.action.desiredAcceleration
    if self.holding:
      self.go_time = self.go_time + self.dt if action > STOP_FINISH_GO_ACTION else 0.
      self.go_asked = self.go_asked or self.go_time >= STOP_FINISH_GO_TIME - 1e-6
      return STOP_FINISH_ACCEL

    if self.lead_time < STOP_FINISH_LEAD_TIME - 1e-6:
      return float('inf')

    vx = model_v2.velocity.x
    self.rise_time = self.rise_time + self.dt if action > 0. else 0.
    if v_ego >= STOP_FINISH_ARM_SPEED and action < STOP_FINISH_ARM_ACTION and len(vx) > 0 and vx[-1] < STOP_FINISH_END_SPEED:
      self.armed = True
    elif v_ego >= STOP_FINISH_SPEED and self.rise_time >= STOP_FINISH_RISE_TIME - 1e-6:
      self.armed = False

    if self.armed and v_ego < STOP_FINISH_SPEED:
      self.holding = True
      return STOP_FINISH_ACCEL
    return float('inf')
