from openpilot.cereal import log
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection

LANE_CHANGE_SPEED_MIN = 20 * CV.MPH_TO_MS
LANE_CHANGE_TIME_MAX = 10.
LANE_CHANGE_START_TIME = 0.5

# Without the push on the wheel, the turn signal alone starts the change. High enough to stay out of the city:
# signalling a left turn on a two-way road must not put the car in the oncoming lane.
AUTO_LANE_CHANGE_SPEED_MIN = 60 * CV.KPH_TO_MS
AUTO_LANE_CHANGE_DELAYS = (1., 2., 3.)  # s, the AutoLaneChangeDelay choices

# Route 32 (03/11), min 4.4 and 59.6, and route 2c: right after a change, turning the stalk off tapped it to the other side,
# and the hand resting on the wheel read as the push for a new change: the car went back. A signal to the other side that
# comes on within REVERSE_WINDOW of a change, or is already on when it ends, waits REVERSE_HOLD with the change off: no
# prompt, no push, no auto timer (owner: "dá um tempinho"). Then only a new push takes the car back, never the auto timer:
# the wheel let go (under RELEASE_TORQUE for RELEASE_TIME: a resting hand hovers around the 50 of steeringPressed) and
# pushed again. That holds for the whole signal, even if the steering drops and comes back meanwhile.
# The hold has to outlast a tap: the stalk's comfort blink lights the lamp 3 times (about 2 s of signal here, with the 0.5 s
# the lamp reading holds) or, if set so in the car, 5 times (about 4 s). It only ever holds back: the change comes after the
# hold only where the old logic would have offered it for that same signal (route 29: on below LANE_CHANGE_SPEED_MIN, never).
REVERSE_WINDOW = 10.  # s after a change ends
REVERSE_HOLD = 4.5  # s the other side's signal must stay on, counted from the end of the change at the earliest
RELEASE_TORQUE = 30.  # steeringTorque, well under steeringPressed's 50 (gwm/carstate.py)
RELEASE_TIME = .5  # s
RELEASE_FRAMES = round(RELEASE_TIME / DT_MDL)  # counted in frames: adding up DT_MDL never quite reaches it


def auto_lane_change_delay(params) -> float | None:
  if not params.get_bool("AutoLaneChange"):
    return None
  choice = params.get("AutoLaneChangeDelay")
  # modeld reads this at startup: a bad value must not stop it from running
  return AUTO_LANE_CHANGE_DELAYS[choice if choice in range(len(AUTO_LANE_CHANGE_DELAYS)) else 1]


class DesireHelper:
  def __init__(self, auto_delay: float | None = None):
    self.auto_delay = auto_delay  # None: only the push on the wheel starts a change
    self.auto_timer = 0.0
    self.auto_armed = False
    self.prev_blinker = False
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.prev_one_blinker = False
    self.desire = log.Desire.none
    self.last_change = LaneChangeDirection.none  # the side of the last change, and how long since it ended
    self.since_change = REVERSE_WINDOW
    self.blinker_side = LaneChangeDirection.none  # the side the signal is on now, and for how long
    self.blinker_time = 0.0
    self.going_back = False  # this signal is to the other side of a change that just ended (until it goes off or over)
    self.back_armed = False  # and the old logic would have offered the change for it: after the hold, it is offered
    self.change_back = False  # this preLaneChange would take the car back: only a new push starts it
    self.released = 0  # frames the wheel has been let go since this preLaneChange began

  @staticmethod
  def get_lane_change_direction(CS):
    return LaneChangeDirection.left if CS.leftBlinker else LaneChangeDirection.right

  def update(self, carstate, lateral_active, lane_change_prob):
    prev_state, prev_direction = self.lane_change_state, self.lane_change_direction
    v_ego = carstate.vEgo
    one_blinker = carstate.leftBlinker != carstate.rightBlinker
    below_lane_change_speed = v_ego < LANE_CHANGE_SPEED_MIN

    # one change for each time the signal goes on: holding it after a change must not start another
    if not one_blinker:
      self.auto_armed = False
    elif not self.prev_blinker and lateral_active:
      self.auto_armed = True
      self.auto_timer = 0.0
    self.prev_blinker = one_blinker
    side = self.get_lane_change_direction(carstate) if one_blinker else LaneChangeDirection.none
    if side != self.blinker_side:  # on, off, or over to the other side
      self.blinker_time = 0.0
      self.going_back = side != LaneChangeDirection.none and self.last_change not in (LaneChangeDirection.none, side) and \
                        self.since_change < REVERSE_WINDOW
      self.back_armed = False
    else:
      self.blinker_time += DT_MDL
    self.blinker_side = side
    self.since_change += DT_MDL

    ended = False  # a change ended the usual way this frame (the model said it's done)
    if not lateral_active or self.lane_change_timer > LANE_CHANGE_TIME_MAX:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
      self.lane_change_timer = 0.0
      self.back_armed = False  # the old logic too drops the offer here, and needs the signal to come on again
    else:
      if below_lane_change_speed:
        self.back_armed = False  # likewise
      held = min(self.blinker_time, self.since_change) >= REVERSE_HOLD
      rising = self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and not below_lane_change_speed
      self.back_armed |= rising and self.going_back  # where the old logic offers the change: here it waits the hold first
      if (rising and not self.going_back) or (self.lane_change_state == LaneChangeState.off and self.back_armed and held):
        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_timer = 0.0
        # Initialize lane change direction to prevent UI alert flicker
        self.lane_change_direction = self.get_lane_change_direction(carstate)
        self.change_back, self.back_armed, self.released = self.going_back, False, 0

      elif self.lane_change_state == LaneChangeState.preLaneChange and self.going_back and not self.change_back:
        # the signal went over to the other side without passing through off: wait the hold, with the change off
        self.lane_change_state = LaneChangeState.off
        self.lane_change_direction = LaneChangeDirection.none
        self.back_armed = one_blinker and not below_lane_change_speed  # the old logic kept offering it, on the new side

      elif self.lane_change_state == LaneChangeState.preLaneChange:
        # Update lane change direction
        self.lane_change_direction = self.get_lane_change_direction(carstate)
        self.change_back &= self.going_back  # over to the side of the last change: an ordinary prompt again

        torque_applied = carstate.steeringPressed and \
                         ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                          (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right))

        blindspot_detected = ((carstate.leftBlindspot and self.lane_change_direction == LaneChangeDirection.left) or
                              (carstate.rightBlindspot and self.lane_change_direction == LaneChangeDirection.right))

        # the whole wait has to be clean: fast enough, blind spot free and nobody on the wheel
        if blindspot_detected or carstate.steeringPressed or v_ego < AUTO_LANE_CHANGE_SPEED_MIN:
          self.auto_timer = 0.0
        else:
          self.auto_timer += DT_MDL
        auto_ready = self.auto_armed and self.auto_delay is not None and self.auto_timer >= self.auto_delay
        if self.change_back:  # going back: never the auto timer, and a push after the wheel was let go
          if self.released < RELEASE_FRAMES:  # once let go long enough, it stays so for this prompt
            self.released = self.released + 1 if abs(carstate.steeringTorque) < RELEASE_TORQUE else 0
          auto_ready = False
          torque_applied = torque_applied and self.released >= RELEASE_FRAMES

        if not one_blinker or below_lane_change_speed:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_timer = 0.0
        elif (torque_applied or auto_ready) and not blindspot_detected:
          self.lane_change_state = LaneChangeState.laneChangeStarting
          self.lane_change_timer = 0.0
          self.auto_armed = False

      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
        self.lane_change_timer += DT_MDL

        if lane_change_prob < 0.02 and self.lane_change_timer >= LANE_CHANGE_START_TIME:
          self.lane_change_timer = 0.0
          ended = True
          if one_blinker and side == self.lane_change_direction:  # still on to the same side: it may chain a second one
            self.lane_change_state = LaneChangeState.preLaneChange
            self.lane_change_direction = self.get_lane_change_direction(carstate)
          else:
            self.lane_change_state = LaneChangeState.off
            self.lane_change_direction = LaneChangeDirection.none

    # however a change ends (done, the steering dropped, timed out), a signal to the other side now waits the hold
    if prev_state == LaneChangeState.laneChangeStarting and self.lane_change_state != LaneChangeState.laneChangeStarting:
      self.last_change, self.since_change = prev_direction, 0.0
      self.going_back = side not in (LaneChangeDirection.none, prev_direction)
      # the old logic offered the other side right away, but only at a usual end, and dropped it below the change speed
      self.back_armed = self.going_back and ended and not below_lane_change_speed
    if self.lane_change_state != LaneChangeState.preLaneChange:
      self.change_back = False

    self.prev_one_blinker = one_blinker and lateral_active

    self.desire = log.Desire.none
    if self.lane_change_state == LaneChangeState.laneChangeStarting:
      if self.lane_change_direction == LaneChangeDirection.left:
        self.desire = log.Desire.laneChangeLeft
      elif self.lane_change_direction == LaneChangeDirection.right:
        self.desire = log.Desire.laneChangeRight
