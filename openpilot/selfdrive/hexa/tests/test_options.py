import re
from pathlib import Path

import pytest

from openpilot.selfdrive.hexa.options import GROUP_KEYS, OPTIONS, HexaOption, get_option, options_in_group

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
    assert o.default in (False, 0), o.key


def test_every_option_is_a_registered_param():
  header = PARAMS_KEYS_H.read_text()
  for key in [o.key for o in OPTIONS] + ["HexaWebPin", "HexaWebTokens", "HexaWebAudit"]:
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
