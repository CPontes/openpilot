from types import SimpleNamespace

import pytest

from opendbc.car.car_helpers import interfaces
from opendbc.car.gwm.values import CAR as GWM
from opendbc.car.structs import car
from opendbc.car.toyota.values import CAR as TOYOTA
from opendbc.car.vehicle_model import VehicleModel
from openpilot.cereal import log
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.hand_follow import HandFollow, LIGHT_TENSION, RELEASE_TIME
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque


def cs(torque=0., angle=60., v=5., pressed=None):
  return SimpleNamespace(steeringTorque=torque, steeringAngleDeg=angle, vEgo=v,
                         steeringPressed=abs(torque) > 50 if pressed is None else pressed)


class TestHandFollow:
  def setup_method(self):
    self.h = HandFollow()

  def run(self, state, seconds):
    r = False
    for _ in range(int(round(seconds / DT_CTRL))):
      r = self.h.update(state)
    return r

  def test_starts_with_the_hand_into_the_curve(self):
    assert self.h.update(cs(80.))
    self.h.reset()
    assert self.h.update(cs(-80., -60.))

  def test_not_against_the_curve(self):
    assert not self.h.update(cs(-80.))

  def test_needs_a_press_to_start(self):
    assert not self.h.update(cs(40.))

  def test_not_on_a_straight(self):
    assert not self.h.update(cs(80., 5.))

  def test_not_at_high_speed(self):
    assert not self.h.update(cs(80., v=20.))

  def test_light_hand_keeps_it(self):
    self.h.update(cs(80.))
    assert self.run(cs(35.), 5.)

  def test_short_gap_keeps_it(self):
    self.h.update(cs(80.))
    assert self.run(cs(10.), RELEASE_TIME - 0.1)
    assert self.run(cs(35.), 0.1)
    assert self.run(cs(10.), RELEASE_TIME - 0.1)

  def test_hand_off_ends_it(self):
    self.h.update(cs(80.))
    assert not self.run(cs(10.), RELEASE_TIME + 0.02)

  def test_hand_against_the_curve_ends_it(self):
    self.h.update(cs(80.))
    assert not self.run(cs(-80.), RELEASE_TIME + 0.02)

  def test_wheel_back_near_center_ends_it(self):
    self.h.update(cs(80.))
    assert not self.h.update(cs(80., 5.))

  def test_speed_ends_it(self):
    self.h.update(cs(80.))
    assert not self.h.update(cs(80., v=20.))

  @pytest.mark.parametrize("output, ff, expected", [
    (1.5, -0.3, -0.3),            # full pull back keeps the feedforward (left curve is negative)
    (1.5, -2., -LIGHT_TENSION),   # and only up to a light tension
    (-0.9, -2., -0.9),            # the torque can still ease above that
    (-0.9, -0.3, -0.9),           # the controller wants more of the curve: stays
    (1.5, 0.2, 0.),               # model wants the other way: never against the hand
  ])
  def test_limit_left_curve(self, output, ff, expected):
    assert self.h.limit(cs(80.), output, ff) == pytest.approx(expected)

  def test_limit_right_curve(self):
    assert self.h.limit(cs(-80., -60.), -1.5, 0.3) == pytest.approx(0.3)


class TestInTheController:
  def torque(self, car_name, hand):
    # a left curve at 11 km/h, with the wheel further in than the model asks for
    CarInterface = interfaces[car_name]
    CP = CarInterface.get_non_essential_params(car_name)
    controller = LatControlTorque(CP.as_reader(), CarInterface(CP), DT_CTRL)
    VM = VehicleModel(CP)
    params = log.VehicleParameters.new_message()
    CS = car.CarState.new_message()
    CS.vEgo = 3.
    CS.steeringAngleDeg = 120.
    CS.steeringTorque = hand
    CS.steeringPressed = abs(hand) > 50
    for _ in range(100):
      torque, _, _ = controller.update(True, CS, VM, params, False, -0.01, False, 0.2)
    return torque

  def test_keeps_a_light_tension_with_the_hand(self):
    assert self.torque(GWM.GWM_HAVAL_H6, 80.) > 0.

  def test_pulls_back_without_the_hand(self):
    assert self.torque(GWM.GWM_HAVAL_H6, 0.) < 0.

  def test_other_cars_keep_pulling_back(self):
    assert self.torque(TOYOTA.TOYOTA_COROLLA_TSS2, 80.) < 0.
