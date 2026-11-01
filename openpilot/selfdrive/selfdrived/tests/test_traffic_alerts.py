from openpilot.selfdrive.selfdrived.traffic_alerts import LEAD_DEPART_DISTANCE, LEAD_DEPART_SPEED, LeadDepartAlert

DT = .01


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
