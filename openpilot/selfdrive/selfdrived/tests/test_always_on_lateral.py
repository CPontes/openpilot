from types import SimpleNamespace

from opendbc.car.gwm.tests.test_always_on import ALWAYS_ON
from opendbc.car.gwm.tests.test_carstate import Car
from openpilot.selfdrive.selfdrived.always_on_lateral import EventName, RESUME_WAIT, LongitudinalPause, ButtonType, reverse_is_parking
from openpilot.selfdrive.selfdrived.events import ET, Events


def cs(brake=False, resume=False, v=0., driving=False):
  events = [SimpleNamespace(type=ButtonType.resumeCruise)] if resume else []
  return SimpleNamespace(brakePressed=brake, buttonEvents=events, vEgo=v, cruiseState=SimpleNamespace(driving=driving))


def reverse_events():
  events = Events()
  events.add(EventName.reverseGear)
  return events


class TestReverseWithTheBrake:
  def test_parking_is_not_an_emergency(self):
    events = reverse_events()
    reverse_is_parking(events, True, True)
    assert EventName.reverseGear not in events.names and EventName.pedalPressed in events.names
    assert events.contains(ET.USER_DISABLE)  # it still disengages
    assert not any(a.alert_text_1 == "TAKE CONTROL IMMEDIATELY" for a in events.create_alerts([ET.USER_DISABLE]))

  def test_without_the_brake_nothing_changes(self):
    events = reverse_events()
    reverse_is_parking(events, True, False)
    assert events.names == [EventName.reverseGear]

  def test_disengaged_nothing_changes(self):
    events = reverse_events()
    reverse_is_parking(events, False, True)
    assert events.names == [EventName.reverseGear]


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


class TestAResumeTheAccHasToTake:
  def paused(self):
    p = LongitudinalPause()
    p.update(True, True, cs(brake=True, v=7.))
    return p

  def test_moving_it_waits_for_the_acc(self):
    p = self.paused()
    assert p.update(True, False, cs(resume=True, v=7.))
    assert p.update(True, False, cs(v=7.))
    assert not p.update(True, False, cs(v=7., driving=True))

  def test_a_pull_the_acc_refused_stays_paused(self):
    p = self.paused()
    p.update(True, False, cs(resume=True, v=7.))
    for _ in range(int(RESUME_WAIT * 100) + 2):
      assert p.update(True, False, cs(v=7.))
    assert p.update(True, False, cs(v=7., driving=True))  # too late: it takes a new pull

  def test_the_acc_already_driving_needs_no_wait(self):
    assert not self.paused().update(True, False, cs(resume=True, v=7., driving=True))

  def test_slow_it_resumes_at_once(self):
    assert not self.paused().update(True, False, cs(resume=True, v=4.))

  def test_the_brake_ends_the_wait(self):
    p = self.paused()
    p.update(True, False, cs(resume=True, v=7.))
    p.update(True, True, cs(brake=True, v=7.))
    p.update(True, False, cs(v=7.))
    assert p.update(True, False, cs(v=7., driving=True))


class TestThroughTheCar:
  def test_lever_ends_the_pause_from_can_frames(self):
    car, p = Car(ALWAYS_ON), LongitudinalPause()
    car.lever()
    assert p.update(True, True, car.brake())
    assert p.update(True, False, car.brake(0))
    assert p.update(True, False, car.pull())
    assert not p.update(True, False, car.release())
    assert not p.update(True, False, car.step())
