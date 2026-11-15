from openpilot.selfdrive.hexa import shadow, timeline


def drive(sc, accel, plan, until=20., brake=None, acc=True, engaged=True, lead=(True, 30.), v=15., fcw_at=()):
  """100 Hz carState, 20 Hz plan: accel(t) is what the Haval did, plan(t) what openpilot asked for."""
  sc.on_selfdrive(engaged)
  sc.on_lead(*lead)
  n = int(until * 100)
  for i in range(n):
    t = i / 100
    if i % 5 == 0:
      sc.on_plan(t, plan(t), any(abs(t - f) < 1e-6 for f in fcw_at))
    sc.on_car(t, 0.01 if i else 0., v, accel(t), bool(brake and brake[0] <= t < brake[1]), acc)
  return sc.result()


def haval_brake(t):  # starts at 10 s, -3 m/s² at 11 s, over at 12 s
  if 10. <= t < 11.:
    return -1.2 - 1.8 * (t - 10.)
  if 11. <= t < 12.:
    return -3.0 + 2.8 * (t - 11.)
  return 0.


def test_openpilot_asked_earlier():
  sc = shadow.ShadowCounter()
  r = drive(sc, haval_brake, lambda t: -1.5 if 8.5 <= t < 12. else 0.)
  assert (r["sh_hard"], r["sh_earlier"], r["sh_weaker"]) == (1, 1, 0)
  assert sc.events == [{"t": 10.0, "peak": -3.0, "op_first": 8.5, "op_at_peak": -1.5, "verdict": "antes"}]


def test_openpilot_would_have_braked_less_is_a_red_flag():
  r = drive(shadow.ShadowCounter(), haval_brake, lambda t: 0.)
  assert (r["sh_hard"], r["sh_earlier"], r["sh_weaker"]) == (1, 0, 1)


def test_the_driver_braking_isnt_the_haval():
  r = drive(shadow.ShadowCounter(), haval_brake, lambda t: 0., brake=(10.5, 10.7))
  assert r["sh_hard"] == 0


def test_only_while_engaged_with_the_acc_driving():
  assert drive(shadow.ShadowCounter(), haval_brake, lambda t: 0., acc=False)["sh_on_s"] == 0
  assert drive(shadow.ShadowCounter(), haval_brake, lambda t: 0., engaged=False)["sh_hard"] == 0
  r = drive(shadow.ShadowCounter(), lambda t: 0., lambda t: 0., until=10.)
  assert r["sh_hard"] == 0 and abs(r["sh_on_s"] - 9.99) < .02


def test_gap_is_time_weighted_and_needs_a_lead():
  r = drive(shadow.ShadowCounter(), lambda t: 0., lambda t: 0., until=10., lead=(True, 22.5), v=15.)
  assert abs(r["sh_gap_s"] / r["sh_gap_t"] - 1.5) < 1e-6
  assert drive(shadow.ShadowCounter(), lambda t: 0., lambda t: 0., until=10., lead=(False, 22.5))["sh_gap_t"] == 0


def test_openpilot_fcw_rising_edges_while_engaged():
  assert drive(shadow.ShadowCounter(), lambda t: 0., lambda t: 0., fcw_at=(3., 3.05, 9.))["sh_fcw"] == 2
  assert drive(shadow.ShadowCounter(), lambda t: 0., lambda t: 0., fcw_at=(3.,), engaged=False)["sh_fcw"] == 0


def test_trip_segment_reads_it_from_real_messages():
  from openpilot.cereal import log
  NS = 1_000_000_000
  events = []

  def add(service, t, fill):
    ev = log.Event.new_message(logMonoTime=int(1_000 * NS + t * NS), valid=True)
    fill(ev.init(service))
    events.append(ev.as_reader())

  for i in range(1500):
    t = i / 100
    a = haval_brake(t)
    add("carState", t, lambda c, a=a: (setattr(c, "vEgo", 15.), setattr(c, "aEgo", a), setattr(c.cruiseState, "driving", True)))
    add("selfdriveState", t, lambda c: setattr(c, "enabled", True))
    add("carControl", t, lambda c: setattr(c, "latActive", True))
    if i % 5 == 0:
      add("longitudinalPlan", t, lambda p, t=t: setattr(p, "aTarget", -1.5 if 8.5 <= t < 12. else 0.))
      add("radarState", t, lambda r: (setattr(r.leadOne, "present", True), setattr(r.leadOne, "dRel", 22.5)))
  s = timeline.trip_segment(events)["summary"]
  assert (s["sh_hard"], s["sh_earlier"], s["sh_weaker"]) == (1, 1, 0)
  assert abs(s["sh_gap_s"] / s["sh_gap_t"] - 1.5) < 1e-3


def test_trip_segment_sees_openpilot_on_the_pedals_from_real_messages():
  from openpilot.cereal import log
  NS = 1_000_000_000
  events = []

  def add(service, t, fill):
    ev = log.Event.new_message(logMonoTime=int(1_000 * NS + t * NS), valid=True)
    fill(ev.init(service))
    events.append(ev.as_reader())

  for i in range(1500):
    t = i / 100
    a = haval_brake(t)
    add("carState", t, lambda c, a=a: (setattr(c, "vEgo", 15.), setattr(c, "aEgo", a), setattr(c.cruiseState, "driving", True)))
    add("selfdriveState", t, lambda c: setattr(c, "enabled", True))
    add("carControl", t, lambda c: setattr(c, "latActive", True))
    if i % 5 == 0:
      add("longitudinalPlan", t, lambda p, a=a: setattr(p, "aTarget", a))
  add("carParams", 14., lambda p: setattr(p, "openpilotLongitudinalControl", True))  # late in the minute: it still counts for all of it
  s = timeline.trip_segment(events)["summary"]
  assert s["sh_hard"] == 0 and s["sh_on_s"] == 0


def test_older_logs_without_the_driving_field_use_enabled():
  sc = shadow.ShadowCounter()
  sc.on_selfdrive(True)
  sc.on_lead(True, 30.)
  for i in range(1500):
    t = i / 100
    if i % 5 == 0:
      sc.on_plan(t, 0., False)
    sc.on_car(t, 0.01, 15., haval_brake(t), False, False, acc_enabled=True)
  assert sc.result()["sh_hard"] == 1
  sc2 = shadow.ShadowCounter()  # once the field shows up, it decides
  sc2.on_selfdrive(True)
  sc2.on_plan(0., 0., False)
  sc2.on_car(0., 0.01, 15., 0., False, True, acc_enabled=True)
  sc2.on_car(0.01, 0.01, 15., 0., False, False, acc_enabled=True)
  assert abs(sc2.result()["sh_on_s"] - 0.01) < 1e-9


def test_with_openpilot_longitudinal_there_is_nothing_to_compare():
  s = shadow.ShadowCounter()
  s.on_op_long(True)
  s.on_selfdrive(True)
  for i in range(200):
    t = i * .05
    s.on_plan(t, -3., False)
    s.on_car(t, .05, 15., -3.5 if 50 < i < 100 else 0., False, True)
  r = s.result()
  assert r["sh_hard"] == 0 and r["sh_on_s"] == 0 and s.events == []


def test_a_plan_from_a_qlog_at_2_hz_still_counts():
  s = shadow.ShadowCounter()
  s.on_selfdrive(True)
  s.on_plan(0., 0., False)
  s.on_car(.6, .05, 15., 0., False, True)
  assert s.result()["sh_on_s"] > 0
