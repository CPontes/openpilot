from types import SimpleNamespace

from openpilot.cereal import log
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.desire_helper import AUTO_LANE_CHANGE_DELAYS, RELEASE_TIME, REVERSE_HOLD, DesireHelper, \
                                                          auto_lane_change_delay

State = log.LaneChangeState
KMH = CV.KPH_TO_MS


def car(kmh=90, left=True, right=False, pressed=False, torque=0., left_blind=False, right_blind=False):
  return SimpleNamespace(vEgo=kmh * KMH, leftBlinker=left, rightBlinker=right, steeringPressed=pressed, steeringTorque=torque,
                         leftBlindspot=left_blind, rightBlindspot=right_blind)


def run(dh, seconds, active=True, prob=0., **kw):
  for _ in range(int(round(seconds / DT_MDL))):
    dh.update(car(**kw), active, prob)
  return dh.lane_change_state


def off(dh, seconds=.5):
  return run(dh, seconds, left=False, right=False)


def test_the_push_still_does_it_without_the_option():
  dh = DesireHelper()
  off(dh)
  assert run(dh, 30.) == State.preLaneChange
  assert run(dh, .1, pressed=True, torque=100.) == State.laneChangeStarting


def test_starts_after_the_delay():
  dh = DesireHelper(2.)
  off(dh)
  assert run(dh, 1.9) == State.preLaneChange
  assert run(dh, .2) == State.laneChangeStarting
  assert dh.lane_change_direction == log.LaneChangeDirection.left


def test_right_side_too():
  dh = DesireHelper(1.)
  off(dh)
  assert run(dh, 1.2, left=False, right=True) == State.laneChangeStarting
  assert dh.desire == log.Desire.laneChangeRight


def test_not_below_60_kmh():
  dh = DesireHelper(1.)
  off(dh)
  assert run(dh, 30., kmh=59) == State.preLaneChange


def test_push_still_works_below_60_kmh():
  dh = DesireHelper(1.)
  off(dh)
  assert run(dh, 5., kmh=40) == State.preLaneChange
  assert run(dh, .1, kmh=40, pressed=True, torque=100.) == State.laneChangeStarting


def test_blind_spot_blocks_and_restarts_the_wait():
  dh = DesireHelper(2.)
  off(dh)
  assert run(dh, 10., left_blind=True) == State.preLaneChange
  assert run(dh, 1.9) == State.preLaneChange
  assert run(dh, .2) == State.laneChangeStarting


def test_other_side_blind_spot_does_not_block():
  dh = DesireHelper(1.)
  off(dh)
  assert run(dh, 1.2, right_blind=True) == State.laneChangeStarting


def test_signal_off_cancels():
  dh = DesireHelper(2.)
  off(dh)
  run(dh, 1.)
  assert off(dh) == State.off
  assert run(dh, 1.) == State.preLaneChange


def test_one_change_for_each_signal():
  dh = DesireHelper(1.)
  off(dh)
  assert run(dh, 1.2) == State.laneChangeStarting
  assert run(dh, .6, prob=.5) == State.laneChangeStarting
  assert run(dh, .1) == State.preLaneChange  # the change ended with the signal still on
  assert run(dh, 30.) == State.preLaneChange


def test_push_can_still_chain_a_second_change():
  dh = DesireHelper(1.)
  off(dh)
  run(dh, 1.2)
  run(dh, .7, prob=.5)
  assert run(dh, .1, pressed=True, torque=100.) == State.laneChangeStarting


def test_new_signal_can_change_again():
  dh = DesireHelper(1.)
  off(dh)
  run(dh, 1.2)
  run(dh, .7, prob=.5)
  off(dh)
  assert run(dh, 1.2) == State.laneChangeStarting


def test_driver_on_the_wheel_stops_the_clock():
  dh = DesireHelper(2.)
  off(dh)
  assert run(dh, 10., pressed=True, torque=-100.) == State.preLaneChange
  assert run(dh, 1.9) == State.preLaneChange
  assert run(dh, .2) == State.laneChangeStarting


def test_needs_steering_active():
  dh = DesireHelper(1.)
  off(dh)
  assert run(dh, 10., active=False) == State.off


def test_signal_left_on_from_before_engaging_does_nothing():
  dh = DesireHelper(1.)
  run(dh, 2., active=False, left=False)
  run(dh, 2., active=False)
  assert run(dh, 30.) == State.preLaneChange


def test_losing_steering_during_a_change_does_not_restart_it():
  dh = DesireHelper(1.)
  off(dh)
  run(dh, 1.2)
  assert run(dh, 1., active=False) == State.off
  assert run(dh, 30.) == State.preLaneChange


class Params:
  def __init__(self, on, choice):
    self.d = {"AutoLaneChange": on, "AutoLaneChangeDelay": choice}

  def get_bool(self, key):
    return self.d[key]

  def get(self, key):
    return self.d[key]


def test_delay_from_the_options():
  assert auto_lane_change_delay(Params(False, 1), "gwm") is None
  assert [auto_lane_change_delay(Params(True, i), "gwm") for i in range(3)] == list(AUTO_LANE_CHANGE_DELAYS) == [1., 2., 3.]


def test_only_in_a_gwm_by_the_car_of_this_drive():
  # the brand modeld has from CarParams: CarParamsPersistent can still say the last car's (a comma moved from a Haval)
  p = Params(True, 1)
  p.d["CarParamsPersistent"] = b"from the last car, never read"
  assert [auto_lane_change_delay(p, b) for b in ("toyota", "hyundai", "", "mock")] == [None] * 4
  assert auto_lane_change_delay(p, "gwm") == 2.


def test_bad_delay_never_stops_modeld():
  from openpilot.selfdrive.controls.lib.desire_helper import auto_lane_change_delay

  class Store:
    def __init__(self, delay):
      self.delay = delay

    def get_bool(self, key):
      return True

    def get(self, key):
      return self.delay

  assert [auto_lane_change_delay(Store(d), "gwm") for d in (0, 1, 2, 7, None)] == [1., 2., 3., 2., 2.]



# ----- going back right after a change (route 32, min 4.6 and 59.7; route 2c)
# The car reads the signal from the lamps and holds it 0.5 s (gwm/carstate.py), so the stalk swinging over shows both sides
# for a moment, and a tap blinks 3 times (about 2 s of signal) or 5 (about 4 s). The hand resting on the wheel crosses the
# push threshold now and then.
L, R = log.LaneChangeDirection.left, log.LaneChangeDirection.right
LEFT, RIGHT = {"left": True, "right": False}, {"left": False, "right": True}
BOTH, NONE = {"left": True, "right": True}, {"left": False, "right": False}
RESTING = {"pressed": True, "torque": -80.}  # toward the right


def drive(dh, steps, states=None):
  """Runs the steps, (seconds, carstate fields + prob/active), and returns every change that started: (second, side)."""
  starts, now = [], 0.
  prev = dh.lane_change_state
  for seconds, fields in steps:
    kw = dict(fields)
    prob, active = kw.pop("prob", 0.), kw.pop("active", True)
    for _ in range(int(round(seconds / DT_MDL))):
      dh.update(car(**kw), active, prob)
      if dh.lane_change_state == State.laneChangeStarting and prev != State.laneChangeStarting:
        starts.append((round(now, 2), dh.lane_change_direction))
      if states is not None:
        states.append(dh.lane_change_state)
      prev = dh.lane_change_state
      now += DT_MDL
  return starts


def left_change(during=LEFT, seconds=3.):
  """The signal on, a push, the model changing lanes, and then it says it's done."""
  return [(.5, NONE), (.3, LEFT), (.1, {**LEFT, "pressed": True, "torque": 100.}), (seconds, {**during, "prob": .5}), (.6, during)]


def sides(starts):
  return [d for _, d in starts]


def test_a_tap_back_with_the_hand_resting_starts_nothing():
  for flashes in (1.9, 3.95):  # 3 or 5 blinks
    dh = DesireHelper(2.)
    st = drive(dh, left_change() + [(.3, BOTH), (flashes, {**RIGHT, **RESTING}), (10., NONE)])
    assert sides(st) == [L], flashes


def test_the_hand_on_and_off_during_a_long_tap_starts_nothing():
  taps = ([(1., {**RIGHT, **RESTING}), (.4, RIGHT), (1., {**RIGHT, **RESTING}), (.4, RIGHT), (1.15, {**RIGHT, **RESTING})],
          [(3.2, {**RIGHT, **RESTING}), (.2, RIGHT), (.55, {**RIGHT, **RESTING})],
          [(3., {**RIGHT, **RESTING}), (.6, RIGHT), (.35, {**RIGHT, **RESTING})])  # 3.95 s each: let go and pushed again late
  for tap in taps:
    dh = DesireHelper(1.)
    assert sides(drive(dh, left_change() + [(.3, BOTH)] + tap + [(5., NONE)])) == [L]


def test_the_signal_jumping_sides_without_both_on_waits_too():
  dh = DesireHelper(2.)
  assert sides(drive(dh, left_change() + [(1.9, {**RIGHT, **RESTING}), (5., NONE)])) == [L]  # still on left after the change


def test_no_prompt_while_it_waits():
  dh = DesireHelper(2.)
  drive(dh, left_change() + [(.3, BOTH)])
  states = []
  drive(dh, [(REVERSE_HOLD - .1, {**RIGHT, **RESTING})], states)
  assert set(states) == {State.off}  # no "steer right" on the screen, nothing to confirm


def test_the_auto_timer_never_takes_it_back():
  dh = DesireHelper(1.)
  assert sides(drive(dh, left_change() + [(.3, BOTH), (20., RIGHT)])) == [L]


def test_going_back_on_purpose_takes_the_hold_and_a_new_push():
  dh = DesireHelper(2.)
  st = drive(dh, left_change() + [(.3, BOTH), (REVERSE_HOLD + 2., {**RIGHT, **RESTING})])
  assert sides(st) == [L]  # the hand never let go: that push began before the hold ended
  st = drive(dh, [(RELEASE_TIME + .1, RIGHT), (.1, {**RIGHT, **RESTING})])
  assert sides(st) == [R]


def test_going_back_after_the_hold_with_a_push():
  dh = DesireHelper()
  drive(dh, left_change() + [(.3, BOTH)])
  assert sides(drive(dh, [(REVERSE_HOLD - .2, RIGHT), (.1, {**RIGHT, **RESTING})])) == []
  assert sides(drive(dh, [(.2 + RELEASE_TIME + .1, RIGHT), (.1, {**RIGHT, **RESTING})])) == [R]


def test_a_change_cut_short_still_counts():
  dh = DesireHelper(2.)
  cut = [(.5, NONE), (.3, LEFT), (.1, {**LEFT, "pressed": True, "torque": 100.}), (1., {**LEFT, "prob": .5}),
         (.2, {**LEFT, "prob": .5, "active": False}), (1., {**LEFT, "prob": .5})]  # the steering dropped for a moment
  assert sides(drive(dh, cut + [(.3, BOTH), (1.9, {**RIGHT, **RESTING}), (5., NONE)])) == [L]
  dh = DesireHelper(2.)  # and when the change runs past its time
  assert sides(drive(dh, left_change(seconds=10.5) + [(.3, BOTH), (1.9, {**RIGHT, **RESTING}), (5., NONE)])) == [L]


def test_a_signal_already_on_the_other_side_waits_from_the_end():
  dh = DesireHelper(2.)
  states = []
  # the change ends in the first frame of the last 0.6 s of left_change: 4.1 s after the end, still inside the hold
  st = drive(dh, left_change(during=RIGHT, seconds=6.) + [(REVERSE_HOLD - 1., {**RIGHT, **RESTING})], states)
  assert sides(st) == [L]
  assert State.preLaneChange not in states[states.index(State.laneChangeStarting):]  # not even a frame of prompt after it
  states = []
  assert sides(drive(dh, [(1., {**RIGHT, **RESTING})], states)) == []  # past the hold: the prompt, but the hand never let go
  assert State.preLaneChange in states
  assert sides(drive(dh, [(RELEASE_TIME + .1, RIGHT), (.1, {**RIGHT, **RESTING})])) == [R]


def test_the_same_side_and_later_changes_are_not_held():
  dh = DesireHelper()
  drive(dh, left_change())
  assert sides(drive(dh, [(.1, {**LEFT, "pressed": True, "torque": 100.})])) == [L]  # a second change to the same side
  dh = DesireHelper()
  drive(dh, left_change() + [(10., NONE)])
  assert sides(drive(dh, [(.2, RIGHT), (.1, {**RIGHT, **RESTING})])) == [R]  # long after: as always


def test_it_never_offers_what_the_old_logic_would_not():  # route 29: the signal came on below the change speed
  dh = DesireHelper(2.)
  slow = {**RIGHT, "kmh": 29}
  hand = [(.5, {**RIGHT, **RESTING, "kmh": 35}), (.3, {**RIGHT, "kmh": 40})] * 6
  st = drive(dh, left_change() + [(1., NONE), (2., {**slow, **RESTING})] + hand)
  assert sides(st) == [L]  # the old logic waits for the signal to come on again above the speed, and so does this
  dh = DesireHelper(2.)  # a change that ends below the change speed with the other side on: the same
  st = drive(dh, left_change() + [(.3, BOTH), (.5, {**RIGHT, "kmh": 29})] + hand)
  assert sides(st) == [L]
  dh = DesireHelper(2.)  # on above the speed, then below it during the hold: the old logic dropped the offer there
  st = drive(dh, left_change() + [(.3, BOTH), (1., RIGHT), (1., {**RIGHT, "kmh": 29}), (REVERSE_HOLD, RIGHT)] + hand)
  assert sides(st) == [L]


def test_the_steering_dropping_during_the_prompt_keeps_it_a_change_back():  # review of 94e9a6d
  dh = DesireHelper(2.)
  drop = [(.1, {**RIGHT, **RESTING, "active": False}), (1., {**RIGHT, **RESTING})]
  assert sides(drive(dh, left_change() + [(.3, BOTH), (REVERSE_HOLD + 1., {**RIGHT, **RESTING})] + drop)) == [L]
  dh = DesireHelper(2.)  # nor the auto timer, after it
  drop = [(.1, {**RIGHT, "active": False}), (5., RIGHT)]
  assert sides(drive(dh, left_change() + [(.3, BOTH), (REVERSE_HOLD + 2.5, RIGHT)] + drop)) == [L]


def test_a_hand_hovering_at_the_threshold_is_not_a_new_push():  # route 32 at 267 s: -52, -50, -52, -68 ... -39
  dh = DesireHelper(2.)
  hover = [(.05, {**RIGHT, "pressed": True, "torque": -55.}), (.05, {**RIGHT, "pressed": False, "torque": -45.})] * 30
  hover += [(.6, {**RIGHT, "pressed": False, "torque": -42.}), (.1, {**RIGHT, **RESTING})]  # under 50 a while: still on the wheel
  hover += [(.05, {**RIGHT, "pressed": False, "torque": -20.}), (.3, {**RIGHT, **RESTING})] * 4  # a dip under 30 for a frame
  assert sides(drive(dh, left_change() + [(.3, BOTH), (REVERSE_HOLD + .5, {**RIGHT, **RESTING})] + hover)) == [L]
  assert sides(drive(dh, [(RELEASE_TIME + .1, RIGHT), (.1, {**RIGHT, **RESTING})])) == [R]  # let go for real, then a push


def test_back_over_to_the_last_side_is_an_ordinary_prompt():
  dh = DesireHelper(1.)
  drive(dh, left_change() + [(.3, BOTH), (REVERSE_HOLD + .5, RIGHT)])  # the change-back prompt
  assert sides(drive(dh, [(1.5, LEFT)])) == [L]  # straight over to the left: as always, the auto timer may chain


def test_other_brands_count_the_hand_off_by_their_own_steering_pressed():
  # RELEASE_TORQUE is in the Haval's units: another car's resting hand may read any number, but steeringPressed is its own
  for torque in (10., 60., 200.):  # under, around and over the Haval's 30
    dh = DesireHelper(2., release_torque=None)
    drive(dh, left_change() + [(.3, BOTH), (REVERSE_HOLD + .1, {**RIGHT, "pressed": True, "torque": -torque})])
    st = drive(dh, [(RELEASE_TIME + .1, {**RIGHT, "torque": -torque}), (.1, {**RIGHT, "pressed": True, "torque": -torque})])
    assert sides(st) == [R], torque  # let go by its own measure, then a push: it goes back
    dh = DesireHelper(2., release_torque=None)
    st = drive(dh, left_change() + [(.3, BOTH), (REVERSE_HOLD + 2., {**RIGHT, "pressed": True, "torque": -torque})])
    assert sides(st) == [L], torque  # never let go: still only the first change
