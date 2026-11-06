from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.haval_shadow import HavalShadow
from openpilot.selfdrive.controls.tests.test_stop_point import planner  # noqa: F401

DT = 0.05
NO_LEAD = SimpleNamespace(present=False, dRel=0.)


def approach(hs, v=11.4, d0=40., v_lead=0., frames=12, lead=NO_LEAD, pedal=False, noise=None):
  # the Haval seeing a car that ends at d0, closing at v - v_lead; returns what the rule asks at the end
  out = 0.
  for k in range(frames):
    d = d0 + (v - v_lead) * DT * (frames - 1 - k)
    if noise is not None:
      d += noise[k]
    out = hs.update(v, d, lead, pedal)
  return out


class TestWhenItWouldAct:
  def test_the_motorcycle_at_the_red_light(self):
    # route 31, min 73.5: 41 km/h, the Haval at 21 m, the model's lead 60 m away or none
    assert approach(HavalShadow(DT), v=11.4, d0=21.) == pytest.approx(-(11.4 ** 2) / (2 * (21. - 7.)), rel=.02)
    assert approach(HavalShadow(DT), v=11.4, d0=21., lead=SimpleNamespace(present=True, dRel=60.)) < -2.

  def test_soft_enough_says_nothing(self):
    # stopping behind it takes under 2 m/s^2
    assert approach(HavalShadow(DT), v=8., d0=40.) == 0.
    assert approach(HavalShadow(DT), v=8., d0=25.) == 0.  # 1.8 m/s^2
    assert approach(HavalShadow(DT), v=8., d0=22.) < -2.  # 2.1 m/s^2

  def test_it_needs_0_6_s_of_the_haval(self):
    assert approach(HavalShadow(DT), v=11.4, d0=21., frames=11) == 0.


class TestWhenItKeepsQuiet:
  def test_the_comma_seeing_the_same_car_is_lead_closings(self):
    assert approach(HavalShadow(DT), v=11.4, d0=21., lead=SimpleNamespace(present=True, dRel=19.)) == 0.

  def test_far_or_near(self):
    assert approach(HavalShadow(DT), v=20., d0=46.) == 0.
    assert approach(HavalShadow(DT), v=8., d0=10.) == 0.

  def test_slow(self):
    assert approach(HavalShadow(DT), v=5.9, d0=12.) == 0.

  def test_a_pedal(self):
    assert approach(HavalShadow(DT), v=11.4, d0=21., pedal=True) == 0.

  def test_a_jump_is_another_target(self):
    noise = np.zeros(12)
    noise[6:] = 3.
    assert approach(HavalShadow(DT), v=11.4, d0=21., noise=noise) == 0.

  def test_a_frame_without_a_target(self):
    hs = HavalShadow(DT)
    for k in range(12):
      hs.update(11.4, 0. if k == 5 else 21. + 11.4 * DT * (11 - k), NO_LEAD, False)
    assert hs.update(11.4, 21. - 11.4 * DT, NO_LEAD, False) == 0.

  def test_the_car_ahead_pulling_away(self):
    assert approach(HavalShadow(DT), v=8., d0=20., v_lead=10.) == 0.
    assert approach(HavalShadow(DT), v=8., d0=12., v_lead=20.) == 0.


class TestInThePlanner:
  def msgs(self, v=11.4, d=21.):
    from opendbc.car.structs import car
    cs = car.CarState.new_message()
    cs.vEgo, cs.vCruise, cs.camLeadDistance = v, 60., d
    ct = log.ControlsState.new_message()
    ct.longControlState = car.CarControl.Actuators.LongControlState.pid
    rd = log.RadarState.new_message()
    sd = log.SelfdriveState.new_message()
    sd.enabled = True
    m = log.ModelDataV2.new_message()
    m.position.x = np.linspace(0., 18., 33).tolist()
    m.velocity.x = np.full(33, v).tolist()
    return {"carState": cs.as_reader(), "controlsState": ct.as_reader(), "radarState": rd.as_reader(), "selfdriveState": sd.as_reader(),
            "modelV2": m.as_reader()}

  def cp(self, brand="gwm", long=True):
    return SimpleNamespace(brand=brand, openpilotLongitudinalControl=long, longitudinalActuatorDelay=0.25)

  def drive(self, p):
    for k in range(20):
      p.update(self.msgs(d=21. + 11.4 * DT * (19 - k)))
    return p

  def test_it_never_changes_the_plan(self, planner):  # noqa: F811
    p = self.drive(planner(self.cp()))
    assert p.haval_shadow_accel < -2.
    q = planner(self.cp())
    q.haval_shadow = None
    self.drive(q)
    assert p.output_a_target == q.output_a_target and p.output_should_stop == q.output_should_stop

  def test_it_goes_to_the_log(self, planner):  # noqa: F811
    from unittest.mock import MagicMock
    p = self.drive(planner(self.cp()))
    sm = MagicMock()
    sm.__getitem__ = lambda _, k: self.msgs()[k]
    sm.logMonoTime = {"modelV2": 0}
    sm.all_checks.return_value = True
    pm = MagicMock()
    p.publish(sm, pm)
    assert pm.send.call_args[0][1].longitudinalPlan.havalShadowAccel == pytest.approx(p.haval_shadow_accel)

  def test_only_the_haval_with_the_comma_longitudinal(self, planner):  # noqa: F811
    assert planner(self.cp(brand="toyota")).haval_shadow is None
    assert planner(self.cp(long=False)).haval_shadow is None

  def test_with_the_longitudinal_off_it_starts_over(self, planner):  # noqa: F811
    p = self.drive(planner(self.cp()))
    from opendbc.car.structs import car
    off = self.msgs()
    ct = log.ControlsState.new_message()
    ct.longControlState = car.CarControl.Actuators.LongControlState.off
    off["controlsState"] = ct.as_reader()
    p.update(off)
    assert p.haval_shadow_accel == 0. and len(p.haval_shadow.distances) == 0
