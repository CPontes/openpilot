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

# Route 32 (03/11), min 4.6 and 59.7: right after a change, turning the stalk off swung it to the other side, and about 1 s
# later the driver's hand on the wheel read as the push for a new change: the car went back. Within REVERSE_WINDOW of a
# change, a signal to the other side starts nothing until it has stayed on for REVERSE_HOLD (owner: "dá um tempinho").
REVERSE_WINDOW = 10.  # s after a change ends
REVERSE_HOLD = 3.  # s the opposite signal must stay on


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

  @staticmethod
  def get_lane_change_direction(CS):
    return LaneChangeDirection.left if CS.leftBlinker else LaneChangeDirection.right

  def update(self, carstate, lateral_active, lane_change_prob):
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
    self.blinker_time = self.blinker_time + DT_MDL if side == self.blinker_side else 0.0
    self.blinker_side = side
    self.since_change += DT_MDL

    if not lateral_active or self.lane_change_timer > LANE_CHANGE_TIME_MAX:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
      self.lane_change_timer = 0.0
    else:
      if self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and not below_lane_change_speed:
        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_timer = 0.0
        # Initialize lane change direction to prevent UI alert flicker
        self.lane_change_direction = self.get_lane_change_direction(carstate)

      elif self.lane_change_state == LaneChangeState.preLaneChange:
        # Update lane change direction
        self.lane_change_direction = self.get_lane_change_direction(carstate)

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
        going_back = self.last_change not in (LaneChangeDirection.none, self.lane_change_direction) and \
                     self.since_change < REVERSE_WINDOW and self.blinker_time < REVERSE_HOLD

        if not one_blinker or below_lane_change_speed:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_timer = 0.0
        elif (torque_applied or auto_ready) and not blindspot_detected and not going_back:
          self.lane_change_state = LaneChangeState.laneChangeStarting
          self.lane_change_timer = 0.0
          self.auto_armed = False

      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
        self.lane_change_timer += DT_MDL

        if lane_change_prob < 0.02 and self.lane_change_timer >= LANE_CHANGE_START_TIME:
          self.lane_change_timer = 0.0
          self.last_change, self.since_change = self.lane_change_direction, 0.0
          if one_blinker:
            self.lane_change_state = LaneChangeState.preLaneChange
            self.lane_change_direction = self.get_lane_change_direction(carstate)
          else:
            self.lane_change_state = LaneChangeState.off
            self.lane_change_direction = LaneChangeDirection.none

    self.prev_one_blinker = one_blinker and lateral_active

    self.desire = log.Desire.none
    if self.lane_change_state == LaneChangeState.laneChangeStarting:
      if self.lane_change_direction == LaneChangeDirection.left:
        self.desire = log.Desire.laneChangeLeft
      elif self.lane_change_direction == LaneChangeDirection.right:
        self.desire = log.Desire.laneChangeRight
