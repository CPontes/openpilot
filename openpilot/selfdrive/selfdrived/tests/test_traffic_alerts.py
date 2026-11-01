from pathlib import Path

from openpilot.cereal import log
from openpilot.selfdrive.selfdrived.traffic_alerts import AUTO_RESUME_MAX_STOP, AUTO_RESUME_REQUEST, LEAD_DEPART_DISTANCE, LEAD_DEPART_SPEED, \
                                                         AutoResume, LeadDepartAlert, driver_attentive

DT = .01
LEAD_DEPART_TIME_MARGIN = .49  # just before the departure counts


class Stop:
  def __init__(self):
    self.alert = LeadDepartAlert(DT)

  def run(self, seconds, d_rel=3.3, v_lead=0., present=True, standstill=True, in_drive=True, gas=False):
    return sum(self.alert.update(standstill, in_drive, gas, present, d_rel, v_lead) for _ in range(int(seconds / DT)))

  def settled(self, d_rel=3.3):
    self.run(3., d_rel=d_rel)
    return self


def test_fires_once_when_the_car_ahead_leaves():
  stop = Stop().settled()
  assert stop.run(5., d_rel=3.3 + LEAD_DEPART_DISTANCE + 1, v_lead=2.) == 1
  assert stop.run(5., d_rel=20., v_lead=4.) == 0


def test_silent_while_the_car_ahead_stays():
  assert Stop().settled().run(60.) == 0


def test_vision_noise_is_not_a_departure():
  stop = Stop().settled()
  assert stop.run(10., d_rel=8., v_lead=0.) == 0  # distance jumps, no speed
  assert stop.run(10., d_rel=4., v_lead=2.) == 0  # speed, but it barely moved
  assert stop.run(.3, d_rel=8., v_lead=2.) == 0  # too short
  assert stop.run(.3) == 0
  assert stop.run(.3, d_rel=8., v_lead=2.) == 0


def test_needs_a_car_at_the_stop():
  stop = Stop()
  assert stop.run(3., present=False) == 0
  assert stop.run(5., d_rel=20., v_lead=3.) == 0


def test_a_far_car_is_not_the_one_we_stopped_behind():
  assert Stop().settled(d_rel=30.).run(5., d_rel=40., v_lead=3.) == 0


def test_needs_two_seconds_stopped():
  stop = Stop()
  assert stop.run(1., d_rel=3.3) == 0
  assert stop.run(.9, d_rel=8., v_lead=3.) == 0


def test_never_while_moving():
  stop = Stop()
  assert stop.run(10., standstill=False, d_rel=20., v_lead=5.) == 0
  stop.settled()
  assert stop.run(.2, standstill=False, d_rel=20., v_lead=5.) == 0
  assert stop.run(1.9, d_rel=20., v_lead=5.) == 0  # moved: this is a new stop, with no reference yet


def test_only_in_drive():
  stop = Stop()
  assert stop.run(10., in_drive=False) == 0
  assert stop.alert.base is None
  stop.settled()
  assert stop.run(5., in_drive=False, d_rel=20., v_lead=5.) == 0


def test_driver_on_the_gas_needs_no_warning():
  assert Stop().settled().run(5., d_rel=20., v_lead=5., gas=True) == 0


def test_next_stop_can_warn_again():
  stop = Stop().settled()
  assert stop.run(1., d_rel=8., v_lead=LEAD_DEPART_SPEED) == 1
  stop.run(1., standstill=False)
  stop.settled()
  assert stop.run(1., d_rel=8., v_lead=LEAD_DEPART_SPEED) == 1


def test_lead_lost_for_a_moment_keeps_the_reference():
  stop = Stop().settled()
  assert stop.run(1., present=False) == 0
  assert stop.run(1., d_rel=8., v_lead=2.) == 1


class Resume:
  GONE = {"d_rel": 3.3 + LEAD_DEPART_DISTANCE + 2, "v_lead": 2.}

  def __init__(self):
    self.auto = AutoResume(DT)

  def run(self, seconds, d_rel=3.3, v_lead=0., present=True, enabled=True, standstill=True, in_drive=True, gas=False, brake=False, attentive=True):
    return sum(self.auto.update(enabled, standstill, in_drive, gas, brake, present, d_rel, v_lead, attentive) for _ in range(int(seconds / DT)))

  def settled(self):
    self.run(3.)
    return self


def test_resume_asks_once_when_the_car_ahead_leaves():
  stop = Resume().settled()
  assert stop.run(5., **Resume.GONE) == int(AUTO_RESUME_REQUEST / DT)
  assert stop.run(5., **Resume.GONE) == 0


def test_resume_stays_quiet_with_the_car_ahead_stopped():
  assert Resume().settled().run(50.) == 0


def test_resume_needs_openpilot_engaged():
  assert Resume().settled().run(5., enabled=False, **Resume.GONE) == 0


def test_resume_needs_the_driver_looking():
  assert Resume().settled().run(5., attentive=False, **Resume.GONE) == 0


def test_resume_not_with_a_pedal():
  assert Resume().settled().run(5., gas=True, **Resume.GONE) == 0
  assert Resume().settled().run(5., brake=True, **Resume.GONE) == 0


def test_a_pedal_during_the_stop_leaves_it_to_the_driver():
  for pedal in ({"gas": True}, {"brake": True}, {"enabled": False}):
    stop = Resume().settled()
    stop.run(.5, **pedal)
    assert stop.run(5., **Resume.GONE) == 0


def test_a_pedal_ends_the_request():
  stop = Resume().settled()
  stop.run(LEAD_DEPART_TIME_MARGIN, **Resume.GONE)
  asked = stop.run(.01, **Resume.GONE)
  assert asked == 1 and stop.run(.5, brake=True, **Resume.GONE) == 0


def test_resume_not_after_a_long_stop():
  stop = Resume()
  stop.run(AUTO_RESUME_MAX_STOP + 1.)
  assert stop.run(5., **Resume.GONE) == 0


def test_resume_just_before_the_limit():
  stop = Resume()
  stop.run(AUTO_RESUME_MAX_STOP - 2.)
  assert stop.run(1.5, **Resume.GONE) > 0


def test_resume_not_in_another_gear():
  assert Resume().settled().run(5., in_drive=False, **Resume.GONE) == 0


def test_moving_ends_the_request_and_a_new_stop_starts_over():
  stop = Resume().settled()
  stop.run(LEAD_DEPART_TIME_MARGIN + .01, **Resume.GONE)
  assert stop.run(.5, standstill=False, **Resume.GONE) == 0
  stop.settled()
  assert stop.run(5., **Resume.GONE) > 0


def test_no_car_ahead_no_resume():
  assert Resume().settled().run(5., present=False, d_rel=0.) == 0
  stop = Resume()
  stop.run(3., present=False, d_rel=0.)
  assert stop.run(5., **Resume.GONE) == 0


def monitoring(policy="vision", alert="none", face=True, distracted=False):
  # a real message: a wrong field here would take selfdrived down on the car
  dm = log.DriverMonitoringState.new_message(activePolicy=policy, alertLevel=alert)
  dm.visionPolicyState.faceDetected = face
  dm.visionPolicyState.isDistracted = distracted
  return dm.as_reader()


def test_attentive_needs_the_camera_seeing_the_driver():
  assert driver_attentive(monitoring())
  assert not driver_attentive(monitoring(face=False))
  assert not driver_attentive(monitoring(distracted=True))
  assert not driver_attentive(monitoring(alert="one"))
  assert not driver_attentive(monitoring(policy="wheeltouch"))


def test_the_phone_panel_never_counts_as_a_driving_process():
  # selfdrived doesn't import on every machine, and this line decides if a dead panel disengages the car
  source = (Path(__file__).parents[1] / "selfdrived.py").read_text()
  assert 'NON_DRIVING_PROCESSES = {"hexaweb"}' in source
  assert "if not p.running and p.shouldBeRunning} - NON_DRIVING_PROCESSES" in source

