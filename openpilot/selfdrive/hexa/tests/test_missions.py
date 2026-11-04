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
  # devolver-volante, parar-perto and fila proved on route 32: out of the route
  assert M.route_keys({}) == ["parar-de-vez", "freio-alavanca", "sinal-abrindo", "lombada-faixa", "frear-antes", "parada-longa", "ruas-casa"]
  routes = [m.route for m in M.CATALOG if m.route]
  assert len(routes) == len(set(routes)) and len(routes) <= M.MAX_PACK
  assert {m.option for m in M.CATALOG if m.version == M.CURRENT} == {"SoftHandoff", "StopCloser"}
  texts = [M.ROUTE[k] for k in ("intro", "during", "back", "after")] + list(M.ROUTE["before"]) + list(M.ROUTE["stop_if"])
  assert "0d09b50 + bf02b93" in M.ROUTE["back"] and not any("b3ac6fe" in x or "CTO" in x for x in texts)
  # proved goes out of the route; one answered parked too
  assert "lombada-faixa" not in M.route_keys({"lombada-faixa": 1}) and "parar-de-vez" in M.route_keys({"parar-de-vez": 2})


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
  st = M.clean({"active": ["lombada-faixa", "portas", "estrada", "fila", "noite", "sempre-ativa"], "answers": {"sempre-ativa": 4}})
  assert st["active"] == ["lombada-faixa", "noite"]  # parked, proved by the catalog (fila too, on route 32), answered in full: out


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


def test_the_history_holds_together():  # owner, 03/11: "saber o quanto performou"
  import re
  for key, tries in M.TRIES.items():
    assert key in M.BY_KEY and tries, key
    assert [t.date for t in tries] == sorted(t.date for t in tries), key  # oldest first
    for t in tries:
      assert re.fullmatch(r"2\d{3}-\d{2}-\d{2}(T\d{2}:\d{2})?", t.date) and t.result in M.TRY_RESULTS and t.route and t.version, key
      assert "T" in t.date or t.result == "base", key  # a day alone would check a later drive's marks too
      assert not re.search(r"-?\d{1,2}[.,]\d{4,}", t.measured), key  # a number, never a place
    # a complete mission says so in its last check; one with only a reference isn't done
    m = M.BY_KEY[key]
    if m.done >= m.total:
      assert any(t.result != "base" for t in tries), key
  # what a drive proved is in the history, with its number
  assert {m.key for m in M.CATALOG if m.done >= m.total or (m.done and m.proof.startswith("rota"))} <= set(M.TRIES)
  pub = {m["key"]: m for m in M.public()["missions"]}
  assert pub["estrada"]["tries"] == [{"date": "2026-11-01T12:56", "route": "rota 16", "version": "17554ba", "result": "ok", "measured": "98,8% engajado"}]
  assert [t.route for t in M.TRIES["cancelar-alavanca"]] == ["rota 16", "rota 1c"]  # 1 + 4: the 5 came from two drives
  assert pub["parar-perto"]["goal"] and pub["parar-perto"]["tries"][0]["result"] == "base"


def test_the_owners_marks_are_kept_for_the_history():
  st = M.clean(None)
  for i in range(M.MAX_PENDING + 5):
    st = M.add_pending(st, "fila", f"m{i}", 1000 + i)
  assert len(st["pending"]) == M.MAX_PENDING and st["pending"][0]["mark"] == "m5" and st["pending"][-1]["mark"] == f"m{M.MAX_PENDING + 4}"
  assert M.MAX_PENDING >= 300  # weeks of marks, not the last 50
