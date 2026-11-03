"""Missions (missions.py): the catalog holds together, and the owner's state can't be bent into anything else."""
import pytest

from openpilot.selfdrive.hexa import missions as M
from openpilot.selfdrive.hexa import options


def test_the_catalog_holds_together():
  keys = [m.key for m in M.CATALOG]
  assert len(keys) == len(set(keys))
  trails = {k for k, _ in M.TRAILS}
  opts = {o.key for o in options.OPTIONS}
  for m in M.CATALOG:
    assert m.trail in trails and m.how in M.HOW and m.total >= 1 and 0 <= m.done <= m.total, m.key
    assert (m.how == "auto") == bool(m.detect), m.key
    assert not m.detect or m.detect in M.DETECT, m.key
    assert not m.option or m.option in opts, m.key  # an option a mission asks for must exist in Ajustes
    assert not m.drive or m.how == "parado", m.key
    assert not m.version or m.version in M.PACKS, m.key
    assert m.done < m.total or m.proof, f"{m.key}: done needs the route that proved it"
    assert not m.when or m.when in M.WHEN, m.key
  assert all(any(m.trail == k for m in M.CATALOG) for k in trails)
  assert [lv[0] for lv in M.LEVELS] == sorted(lv[0] for lv in M.LEVELS) and M.LEVELS[-1][0] == 1.


def test_the_ctos_route_for_b3ac6fe_is_the_pack_in_its_order():
  pack = [m.key for m in M.CATALOG if m.version == "b3ac6fe"]
  assert pack[:8] == ["parar-de-vez", "freio-alavanca", "sinal-abrindo", "fila", "lombada-faixa", "frear-antes", "parada-longa", "ruas-casa"]
  assert len(M.PACKS["b3ac6fe"]["stop_if"]) == 3 and "a635314" in M.PACKS["b3ac6fe"]["back"]


def test_take_drop_and_the_limit():
  st = M.clean(None)
  for k in ("transito-lento", "parar-de-vez", "sair-sozinho"):
    st = M.take(st, k)
  with pytest.raises(ValueError):
    M.take(st, "noite")
  assert M.take(st, "transito-lento") == st  # twice is once
  st = M.drop(st, "parar-de-vez")
  assert st["active"] == ["transito-lento", "sair-sozinho"]
  for k in ("cancelar-comma", "estrada", "parear-casa", "nao-existe"):  # locked, done by the CTO, parked, unknown
    with pytest.raises(ValueError):
      M.take(st, k)


def test_a_version_route_goes_whole():
  st = M.take_pack(M.take(M.clean(None), "noite"), "b3ac6fe")
  assert st["active"][0] == "parar-de-vez" and "noite" not in st["active"] and len(st["active"]) > M.MAX_ACTIVE
  assert M.clean(st) == st


def test_answers_only_for_parked_missions_and_never_past_the_total():
  st = M.clean(None)
  st = M.answer(st, "portas", True)
  st = M.answer(st, "portas", False)
  assert st["answers"]["portas"] == 1
  for _ in range(9):
    st = M.answer(st, "portas", True)
  assert st["answers"]["portas"] == 4
  with pytest.raises(ValueError):
    M.answer(st, "transito-lento", True)  # the comma's to say
  st = M.answer(M.take(st, "aviso-acc"), "aviso-acc", True)
  assert "aviso-acc" not in st["active"]  # a drive question leaves the list once answered


def test_clean_drops_what_isnt_a_mission():
  raw = {"active": ["noite", "x", 3, "noite"], "answers": {"portas": 99, "x": 1, "cinto": True, "pedais": -2},
         "pending": [{"key": "fila", "mark": "ab", "t": 1}, {"key": "x"}, "y"]}
  st = M.clean(raw)
  assert st == {"active": ["noite"], "answers": {"portas": 4, "pedais": 0}, "pending": [{"key": "fila", "mark": "ab", "t": 1}]}
