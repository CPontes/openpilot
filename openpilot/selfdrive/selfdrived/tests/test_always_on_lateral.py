from types import SimpleNamespace

from opendbc.car.gwm.tests.test_always_on import ALWAYS_ON
from opendbc.car.gwm.tests.test_carstate import Car
from openpilot.selfdrive.selfdrived.always_on_lateral import LongitudinalPause, ButtonType


def cs(brake=False, resume=False):
  events = [SimpleNamespace(type=ButtonType.resumeCruise)] if resume else []
  return SimpleNamespace(brakePressed=brake, buttonEvents=events)


class TestLongitudinalPause:
  def test_brake_pauses_until_resume(self):
    p = LongitudinalPause()
    assert not p.update(True, False, cs())
    assert p.update(True, True, cs(brake=True))   # brake press
    assert p.update(True, False, cs())             # released: still paused
    assert not p.update(True, False, cs(resume=True))
    assert not p.update(True, False, cs())

  def test_brake_held_overrides(self):
    p = LongitudinalPause()
    assert p.update(True, False, cs(brake=True))

  def test_disengage_clears(self):
    p = LongitudinalPause()
    p.update(True, True, cs(brake=True))
    p.update(False, False, cs())
    assert not p.update(True, False, cs())


class TestThroughTheCar:
  def test_lever_ends_the_pause_from_can_frames(self):
    car, p = Car(ALWAYS_ON), LongitudinalPause()
    car.lever()
    assert p.update(True, True, car.brake())
    assert p.update(True, False, car.brake(0))
    assert p.update(True, False, car.pull())
    assert not p.update(True, False, car.release())
    assert not p.update(True, False, car.step())
