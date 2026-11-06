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
