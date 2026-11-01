from types import SimpleNamespace

from openpilot.cereal import log
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.desire_helper import AUTO_LANE_CHANGE_DELAYS, DesireHelper, auto_lane_change_delay

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
  assert auto_lane_change_delay(Params(False, 1)) is None
  assert [auto_lane_change_delay(Params(True, i)) for i in range(3)] == list(AUTO_LANE_CHANGE_DELAYS) == [1., 2., 3.]


def test_bad_delay_never_stops_modeld():
  from openpilot.selfdrive.controls.lib.desire_helper import auto_lane_change_delay

  class Store:
    def __init__(self, delay):
      self.delay = delay

    def get_bool(self, key):
      return True

    def get(self, key):
      return self.delay

  assert [auto_lane_change_delay(Store(d)) for d in (0, 1, 2, 7, None)] == [1., 2., 3., 2., 2.]
