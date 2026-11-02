from openpilot.selfdrive.hexa import comma_ctl, confirm
from openpilot.selfdrive.hexa.phone import VehicleState


class Store(dict):
  def get_bool(self, k):
    return bool(self.get(k, False))

  def put_bool(self, k, v):
    self[k] = bool(v)


OFF = VehicleState(onroad=False)
PARKED = VehicleState(onroad=True, parked=True, standstill=True, engaged=False)
DRIVING = VehicleState(onroad=True, parked=False, standstill=False, engaged=True)


def test_every_item_is_one_of_the_levels_and_texts_are_complete():
  keys = [i.key for i in comma_ctl.ITEMS]
  assert len(keys) == len(set(keys))
  for it in comma_ctl.ITEMS:
    assert it.level in comma_ctl.LEVELS and it.group in dict(comma_ctl.GROUPS)
    assert it.title and it.description.endswith(".")
    assert (it.level == "trav") == bool(it.blocked) or it.key == "LongitudinalManeuverMode"


def test_what_opens_the_device_or_changes_openpilot_needs_the_comma():
  level = {i.key: i.level for i in comma_ctl.ITEMS}
  assert level["SshEnabled"] == "conf" and level["OpenpilotEnabledToggle"] == "conf" and level["LateralManeuverMode"] == "conf"
  assert level["AdbEnabled"] == "so" and level["JoystickDebugMode"] == "so"
  assert level["AlphaLongitudinalEnabled"] == "trav"  # CTO, UI-07: no longitudinal switch on the phone before LONG-02
  alpha = comma_ctl.get_item("AlphaLongitudinalEnabled")
  assert any("AEB" in w for w in alpha.warnings)  # the comma's own warning, developer.py:23


def test_car_off_or_in_p_and_the_comma_locks():
  s = Store()
  metric = comma_ctl.get_item("IsMetric")
  assert comma_ctl.can_change(metric, OFF, s) == (True, "")
  assert comma_ctl.can_change(metric, PARKED, s) == (True, "")
  assert comma_ctl.can_change(metric, DRIVING, s)[0] is False
  lateral = comma_ctl.get_item("LateralManeuverMode")
  assert comma_ctl.can_change(lateral, PARKED, s) == (False, "Desligue o carro.")  # the comma allows it only offroad
  front = comma_ctl.get_item("RecordFront")
  s["RecordFrontLock"] = True
  assert comma_ctl.can_change(front, OFF, s) == (False, "Travado no comma.")
  assert comma_ctl.can_change(comma_ctl.get_item("AdbEnabled"), OFF, s)[0] is False


def test_release_builds_hide_what_the_comma_hides():
  shown = {i.key for i in comma_ctl.ITEMS if comma_ctl.visible(i, release=True)}
  assert "AlphaLongitudinalEnabled" not in shown and "LateralManeuverMode" not in shown and "SshEnabled" in shown


def test_side_effects_like_the_comma_screen():
  s = Store(JoystickDebugMode=True, LongitudinalManeuverMode=True)
  comma_ctl.apply(comma_ctl.get_item("LateralManeuverMode"), True, s)
  assert s == {"JoystickDebugMode": False, "LongitudinalManeuverMode": False, "LateralManeuverMode": True}
  comma_ctl.apply(comma_ctl.get_item("LateralManeuverMode"), False, s)
  assert s["LateralManeuverMode"] is False and s["JoystickDebugMode"] is False


def test_confirm_request_is_single_and_fresh():
  req = confirm.new_request({"kind": "set"}, "ligar: acesso SSH", "iPhone", now=100.)
  assert len(req["code"]) == 4 and req["code"].isdigit() and req["expires"] == 100. + confirm.CONFIRM_S
  assert confirm.parse(req) == req and confirm.parse('{"id": 1}') is None and confirm.parse(b"not json") is None
  assert confirm.confirmed(req, req["id"], now=120.)
  assert confirm.confirmed(req, req["id"].encode(), now=120.)
  assert not confirm.confirmed(req, "other", now=120.)
  assert not confirm.confirmed(req, req["id"], now=100. + confirm.CONFIRM_S + 1)
  assert confirm.seconds_left(req, now=130.) == 30
