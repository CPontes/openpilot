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
    assert not m.route or M.can_take(m), m.key  # the route only holds what goes on a drive
    assert m.done < m.total or m.proof, f"{m.key}: done needs the route that proved it"
    assert not m.when or m.when in M.WHEN, m.key
  assert all(any(m.trail == k for m in M.CATALOG) for k in trails)
  assert [lv[0] for lv in M.LEVELS] == sorted(lv[0] for lv in M.LEVELS) and M.LEVELS[-1][0] == 1.


def test_one_route_new_tests_first_then_what_an_older_version_left_open():  # owner, 03/11
  assert M.route_keys({}) == ["devolver-volante", "parar-perto", "parar-de-vez", "freio-alavanca", "sinal-abrindo", "fila", "lombada-faixa",
                              "frear-antes", "parada-longa", "ruas-casa"]
  routes = [m.route for m in M.CATALOG if m.route]
  assert len(routes) == len(set(routes)) and len(routes) <= M.MAX_PACK
  assert {m.option for m in M.CATALOG if m.version == M.CURRENT} == {"SoftHandoff", "StopCloser"}
  texts = [M.ROUTE[k] for k in ("intro", "during", "back", "after")] + list(M.ROUTE["before"]) + list(M.ROUTE["stop_if"])
  assert "0d09b50 + bf02b93" in M.ROUTE["back"] and not any("b3ac6fe" in x or "CTO" in x for x in texts)
  # proved goes out of the route; one answered parked too
  assert "fila" not in M.route_keys({"fila": 1}) and "parar-de-vez" in M.route_keys({"parar-de-vez": 2})


def test_take_drop_and_the_limit():
  st = M.clean(None)
  for k in ("transito-lento", "parar-de-vez", "sair-sozinho"):
    st = M.take(st, k)
  st = M.take(st, "noite")  # more than 3: the owner picks as many as fit a drive
  full = dict(st, active=[m.key for m in M.CATALOG if M.can_take(m)][:M.MAX_ACTIVE])
  with pytest.raises(ValueError):
    M.take(full, next(m.key for m in M.CATALOG if M.can_take(m) and m.key not in full["active"]))
  st = M.drop(st, "noite")
  assert M.take(st, "transito-lento") == st  # twice is once
  st = M.drop(st, "parar-de-vez")
  assert st["active"] == ["transito-lento", "sair-sozinho"]
  for k in ("cancelar-comma", "estrada", "parear-casa", "nao-existe"):  # locked, done by the CTO, parked, unknown
    with pytest.raises(ValueError):
      M.take(st, k)


def test_the_route_goes_after_what_was_picked():
  st = M.take_pack(M.take(M.clean(None), "noite"), M.ROUTE_KEY)
  assert st["active"] == ["noite"] + M.route_keys({}) and M.clean(st) == st
  assert M.take_pack(st, M.ROUTE_KEY) == st  # twice is once
  assert M.take_pack(M.clean(None), "b3ac6fe")["active"] == M.route_keys({})  # chosen away from the comma before this version
  with pytest.raises(ValueError):
    M.take_pack(st, "x")
  many = dict(st, active=[m.key for m in M.CATALOG if M.can_take(m) and not m.route][:5])
  assert M.take_pack(many, M.ROUTE_KEY)["active"] == many["active"] + M.route_keys({})[:7]  # never past the limit, the route's order kept


def test_what_was_proved_since_it_was_taken_leaves_the_trip():  # CTO review: it held one of the 12 places with no way out
  st = M.clean({"active": ["fila", "portas", "estrada", "noite", "sempre-ativa"], "answers": {"sempre-ativa": 4}})
  assert st["active"] == ["fila", "noite"]  # parked, proved by the catalog, answered in full: out


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
