import re
from pathlib import Path

import pytest

from openpilot.selfdrive.hexa import options
from openpilot.selfdrive.hexa.options import GROUP_KEYS, OPTIONS, HexaOption, get_option, is_on, locked_while_engaged, options_in_group, supported

PARAMS_KEYS_H = Path(__file__).parents[3] / "common" / "params_keys.h"


def test_keys_unique_and_groups_known():
  keys = [o.key for o in OPTIONS]
  assert len(keys) == len(set(keys))
  for o in OPTIONS:
    assert o.group in GROUP_KEYS, o.key
    assert o.title == o.title.lower(), "comma 4 UI titles are lowercase"
    assert o.description


def test_every_option_is_off_by_default():
  for o in OPTIONS:
    # a fix the car needs stays on, and switchable: everything else starts off
    assert o.default in (False, 0) or o.key == "LowSpeedFilter" or o.kind == "enum", o.key


def test_every_option_is_a_registered_param():
  header = PARAMS_KEYS_H.read_text()
  for key in [o.key for o in OPTIONS] + ["HexaWebPin", "HexaWebTokens", "HexaWebAudit", "HexaMarks", "HexaMarkReasons", "HexaBookmarkRequest"]:
    m = re.search(r'\{"' + re.escape(key) + r'", \{([^}]*)\}', header)
    assert m, f"{key} missing from params_keys.h"
    if get_option(key) is not None and get_option(key).kind == "bool":
      assert "BOOL" in m.group(1), key


def test_phone_panel_cannot_be_turned_on_from_the_phone():
  assert get_option("HexaWebEnabled").phone_writable is False


def test_driving_options_are_marked():
  for key in ("AlwaysOnLateral", "LaneOffsetEnabled"):
    assert get_option(key).driving


def test_groups_lookup():
  assert {o.key for o in options_in_group("direcao")} >= {"AlwaysOnLateral", "LaneOffsetEnabled"}
  assert get_option("nope") is None


def test_coerce_bool():
  o = HexaOption("K", "direcao", "k", "d")
  assert o.coerce(True) is True
  for bad in (1, 0, "true", None, "1"):
    with pytest.raises(ValueError):
      o.coerce(bad)


def test_coerce_enum():
  o = HexaOption("K", "direcao", "k", "d", kind="enum", choices=("a", "b", "c"), default=0)
  assert o.coerce(2) == 2
  for bad in (-1, 3, True, "1", 1.0, None):
    with pytest.raises(ValueError):
      o.coerce(bad)


def test_option_without_its_param_in_this_build_is_hidden():
  from openpilot.selfdrive.hexa.options import supported

  class BuildParams:
    def check_key(self, key):
      if key == "LaneOffsetEnabled":
        raise KeyError(key)  # Params raises UnknownKeyName for keys the build doesn't have
      return key

  shown = [o.key for o in OPTIONS if supported(o, BuildParams())]
  assert "LaneOffsetEnabled" not in shown and "HexaWebEnabled" in shown
  # stores without check_key know every key
  assert all(supported(o, object()) for o in OPTIONS if not o.blocked)


def test_always_on_lateral_is_offered_and_starts_off():
  opt = get_option("AlwaysOnLateral")
  assert supported(opt, object()) and not opt.blocked and opt.default is False and opt.restart_needed
  assert is_on(Stored(), "AlwaysOnLateral") is False
  assert is_on(Stored(AlwaysOnLateral=True), "AlwaysOnLateral") is True


def test_bump_slowdown_is_offered_and_starts_off():
  opt = get_option("BumpSlowdown")
  assert opt.default is False and opt.restart_needed and opt.driving and not opt.blocked


class Stored:
  def __init__(self, **values):
    self.values = values

  def get_bool(self, key):
    return self.values.get(key, False)


def test_a_blocked_option_stays_off_even_if_it_was_on(monkeypatch):
  blocked = HexaOption(key="AutoResume", group="velocidade", title="x", description="x", blocked="not proven")
  monkeypatch.setattr(options, "get_option", lambda key: blocked)
  assert is_on(Stored(AutoResume=True), "AutoResume") is False


def test_a_normal_option_reads_its_param():
  assert is_on(Stored(AutoResume=True), "AutoResume") is True
  assert is_on(Stored(), "AutoResume") is False
  assert is_on(Stored(Unknown=True), "Unknown") is False



def test_what_restarts_openpilot_is_locked_while_engaged():  # review of the 3X page: an alert's restart mid-drive
  # hardwared drops "started" for a second on OnroadCycleRequested, with no look at engaged: controlsd and the rest stop
  for o in OPTIONS:
    assert locked_while_engaged(o) == (o.driving or o.restart_needed), o.key
  assert {o.key for o in OPTIONS if o.restart_needed and not o.driving} >= {"RadarAlert", "LeadDepartAlert", "AccLostAlert"}
  assert all(locked_while_engaged(o) for o in OPTIONS if o.restart_needed)
  # both comma screens lock by this rule, not by "driving" alone
  ui = Path(__file__).parents[2] / "ui"
  for page in (ui / "mici" / "layouts" / "settings" / "hexa.py", ui / "layouts" / "settings" / "hexa.py"):
    src = page.read_text()
    assert "if locked_while_engaged(opt):" in src and "if opt.driving:" not in src, page
