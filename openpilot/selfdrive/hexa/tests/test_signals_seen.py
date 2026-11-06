"""The car's signals seen changing, for the comma approval checks (signals_seen.py, MISS-01)."""
from types import SimpleNamespace

from openpilot.selfdrive.hexa import signals_seen as sg


def cs(**kw):
  base = {"gearShifter": "park", "vEgo": 0., "seatbeltUnlatched": False, "doorOpen": False, "brakePressed": False,
          "gasPressed": False, "leftBlinker": False, "rightBlinker": False, "leftBlindspot": False, "rightBlindspot": False,
          "steerFaultTemporary": False, "steerFaultPermanent": False}
  return SimpleNamespace(**{**base, **kw})


def feed(*states):
  s = sg.SignalsSeen()
  for st in states:
    s.on_car(st)
  return s.result()


def test_nothing_changes_nothing_counts():
  r = feed(*[cs()] * 20)
  assert all(r[k] == 0 for k in sg.COUNT_KEYS) and r["sg_gears"] == sg.GEAR_BITS["park"]


def test_a_state_held_from_the_start_is_no_change():
  # the minute starts with the door already open: no opening seen in it
  assert feed(cs(doorOpen=True), cs(doorOpen=True))["sg_door"] == 0


def test_the_seatbelt_counts_when_fastened_again():
  r = feed(cs(), cs(seatbeltUnlatched=True), cs(seatbeltUnlatched=True), cs())
  assert r["sg_belt"] == 1
  assert feed(cs(), cs(seatbeltUnlatched=True))["sg_belt"] == 0  # off and not on again yet


def test_each_door_opening_counts():
  r = feed(cs(), *[s for _ in range(4) for s in (cs(doorOpen=True), cs())])
  assert r["sg_door"] == 4


def test_the_four_gears_and_another():
  r = feed(*[cs(gearShifter=g) for g in ("park", "reverse", "neutral", "drive", "sport")])
  assert r["sg_gears"] == 0b11111
  assert feed(cs(gearShifter="unknown"))["sg_gears"] == 0  # unknown is no position


def test_the_pedals_count_only_parked():
  r = feed(cs(), cs(brakePressed=True), cs(), cs(gasPressed=True), cs(), cs(vEgo=5.), cs(vEgo=5., brakePressed=True))
  assert r["sg_brake"] == 1 and r["sg_gas"] == 1


def test_turn_signals_and_the_hazard_apart():
  r = feed(cs(), cs(leftBlinker=True), cs(), cs(rightBlinker=True), cs(), cs(leftBlinker=True, rightBlinker=True))
  assert (r["sg_left"], r["sg_right"], r["sg_hazard"]) == (1, 1, 1)


def test_blind_spots_each_side():
  r = feed(cs(), cs(leftBlindspot=True), cs(), cs(rightBlindspot=True), cs(rightBlindspot=False))
  assert r["sg_bsm_l"] == 1 and r["sg_bsm_r"] == 1


def test_a_steering_fault_counts_once():
  r = feed(cs(), cs(steerFaultTemporary=True), cs(steerFaultTemporary=True), cs(), cs(steerFaultPermanent=True))
  assert r["sg_steer_fault"] == 2


def test_minutes_add_up_and_gears_join():
  total = {}
  sg.merge(total, {"sg_door": 2, "sg_gears": 1})
  sg.merge(total, {"sg_door": 3, "sg_gears": 8})
  assert total["sg_door"] == 5 and total["sg_gears"] == 9 and total["sg_belt"] == 0


def test_what_the_comma_learned_in_a_drive():  # MISS-01 C
  c = sg.CalibrationSeen()
  c.on_calibration(SimpleNamespace(calStatus="uncalibrated", calPerc=40))
  c.on_calibration(SimpleNamespace(calStatus="calibrated", calPerc=100))
  c.on_parameters(SimpleNamespace(valid=True, steerRatio=18.413))
  c.on_torque(SimpleNamespace(calPerc=57, useParams=True, latAccelFactorFiltered=1.093))
  c.on_torque(SimpleNamespace(calPerc=20, useParams=False, latAccelFactorFiltered=0.))  # a lower one later: the most stays
  for i in range(100):
    t = i / 10
    c.on_gps(t, SimpleNamespace(hasFix=True, speed=20.))
    c.on_car(t, 20.4, .1)
  r = c.result()
  assert r["cb_calibrated"] == 1 and r["cb_cal_perc"] == 100 and r["cb_sr"] == 18.41 and r["cb_tq_perc"] == 57 and r["cb_tq_laf"] == 1.09
  assert abs(r["cb_sr_km"] - .204) < .001 and r["cb_v_n"] == 100 and abs(r["cb_v_wheel"] / r["cb_v_gps"] - 1.02) < 1e-6


def test_the_wheels_against_the_gps_only_with_a_fresh_fix_and_speed():
  c = sg.CalibrationSeen()
  c.on_gps(0., SimpleNamespace(hasFix=True, speed=20.))
  c.on_car(5., 20., .1)  # the fix is 5 s old
  c.on_gps(5., SimpleNamespace(hasFix=False, speed=20.))
  c.on_car(5.1, 20., .1)
  c.on_gps(6., SimpleNamespace(hasFix=True, speed=5.))
  c.on_car(6.1, 5., .1)  # too slow to compare
  c.on_parameters(SimpleNamespace(valid=False, steerRatio=17.))
  assert c.result()["cb_v_n"] == 0 and "cb_sr" not in c.result()


def test_learned_minutes_join():
  total = {}
  sg.merge_calibration(total, {"cb_sr_km": 1., "cb_tq_perc": 57., "cb_sr": 18.1, "cb_v_n": 10})
  sg.merge_calibration(total, {"cb_sr_km": 2., "cb_tq_perc": 20., "cb_v_n": 5})
  assert total["cb_sr_km"] == 3. and total["cb_tq_perc"] == 57. and total["cb_sr"] == 18.1 and total["cb_v_n"] == 15
