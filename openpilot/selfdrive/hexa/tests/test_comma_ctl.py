from openpilot.selfdrive.hexa import comma_ctl
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


def test_what_the_owner_left_out_is_not_on_the_phone():
  keys = {i.key for i in comma_ctl.ITEMS}
  assert not keys & {"SshEnabled", "RecordFront", "RecordAudio", "LateralManeuverMode"}
  level = {i.key: i.level for i in comma_ctl.ITEMS}
  assert level["OpenpilotEnabledToggle"] == "so" and level["AdbEnabled"] == "so" and level["JoystickDebugMode"] == "so"
  assert level["AlphaLongitudinalEnabled"] == "trav"  # CTO, UI-07: no longitudinal switch on the phone before LONG-02
  alpha = comma_ctl.get_item("AlphaLongitudinalEnabled")
  assert any("AEB" in w for w in alpha.warnings)  # the comma's own warning, developer.py:23


def test_car_off_or_in_p_and_the_comma_locks():
  s = Store()
  metric = comma_ctl.get_item("IsMetric")
  assert comma_ctl.can_change(metric, OFF, s) == (True, "")
  assert comma_ctl.can_change(metric, PARKED, s) == (True, "")
  assert comma_ctl.can_change(metric, DRIVING, s)[0] is False
  dm = comma_ctl.get_item("AlwaysOnDM")
  s["AlwaysOnDMLock"] = True
  assert comma_ctl.can_change(dm, OFF, s) == (False, "Travado no comma.")
  assert comma_ctl.can_change(comma_ctl.get_item("AdbEnabled"), OFF, s)[0] is False


def test_release_builds_hide_what_the_comma_hides():
  shown = {i.key for i in comma_ctl.ITEMS if comma_ctl.visible(i, release=True)}
  assert "AlphaLongitudinalEnabled" not in shown and "NavigationEnabled" not in shown and "AdbEnabled" in shown


def test_nothing_here_starts_a_process_the_drive_watches():
  assert comma_ctl.get_item("NavigationEnabled").level == "so"  # navd is in selfdrived's process check


def test_the_longitudinal_texts_are_true_with_it_on_or_off():
  alpha = comma_ctl.get_item("AlphaLongitudinalEnabled")
  text = " ".join((alpha.description, alpha.blocked, *alpha.warnings))
  assert "AEB" in text and "ACC original." not in " ".join(alpha.warnings) and "Travado até" not in text
  assert comma_ctl.get_item("LongitudinalManeuverMode").level == "so"
