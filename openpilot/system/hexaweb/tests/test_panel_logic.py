"""The phone panel's decisions (static/lp-logic.js), run in node: what the band says, what the comma card claims,
the speed bump messages and the passenger question. Skipped where node isn't installed (the comma itself)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

LOGIC = Path(__file__).parents[1] / "static" / "lp-logic.js"
NODE = shutil.which("node") or next((p for p in ("/opt/node22/bin/node", "/usr/local/bin/node") if Path(p).exists()), None)
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def run(fn: str, *args):
  script = f"const L = require({json.dumps(str(LOGIC))}); process.stdout.write(JSON.stringify(L[{json.dumps(fn)}](...{json.dumps(args)})));"
  out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, check=True).stdout
  return json.loads(out)


T0 = 1_000_000
NOW = {"online": True, "age": 500, "now": T0}
DRIVING = {"driving": True, "v": 62, "steering": True, "engaged": True, "driver": False, "acc": True, "acc_active": True, "acc_set": 70,
           "alert": "", "alert_status": "normal", "why": []}


def live(**kw):
  return {**DRIVING, **kw}


def at(ms):
  return {**NOW, "now": T0 + ms}


# ----- the band

def test_openpilot_driving_with_the_acc_in_control():
  b = run("band", live(), NOW)
  assert b["cls"] == "op" and b["who"] == "openpilot dirigindo" and b["acc"] == 70


def test_acc_number_only_with_the_acc_in_control():
  assert run("band", live(acc_active=False), NOW)["acc"] is None
  assert run("band", live(acc_active=None), NOW)["acc"] is None
  assert run("band", live(acc_set=None), NOW)["acc"] is None
  assert run("band", live(steering=False), NOW)["acc"] == 70  # the driver steering with the ACC on


def test_steering_only_needs_2_s_moving():
  first = run("band", live(acc_active=False), NOW)
  assert first["cls"] == "op" and first["who"] == "openpilot na direção" and first["latSince"] == T0  # foot on the gas: not yet
  assert run("band", live(acc_active=False), at(1500), first["latSince"])["cls"] == "op"
  b = run("band", live(acc_active=False), at(2000), first["latSince"])
  assert b["cls"] == "lat" and b["who"] == "só direção · a velocidade é sua" and b["sub"] == "ACC desligado" and b["acc"] is None
  back = run("band", live(), at(2500), b["latSince"])  # the ACC back in control resets it
  assert back["cls"] == "op" and back["latSince"] is None


def test_stopped_says_nothing_about_the_acc():
  # [log] engaged with acc_active false is mostly stopped behind a car, held by the ACC (state 2)
  b = run("band", live(v=0, acc_active=False), at(10_000), T0)
  assert b["cls"] == "op" and b["who"] == "openpilot na direção" and b["acc"] is None and b["latSince"] is None
  assert run("band", live(v=0), NOW)["who"] == "openpilot na direção"


def test_unknown_acc_state_is_never_claimed():
  assert run("band", live(acc_active=None), NOW)["who"] == "openpilot na direção"


def test_alert_is_amber_and_critical_is_red():
  assert run("band", live(alert="Steer Unavailable", alert_status="userPrompt"), NOW)["cls"] == "warn"
  b = run("band", live(alert="TAKE CONTROL IMMEDIATELY", alert_status="critical"), NOW)
  assert b["cls"] == "fail" and b["who"] == "assuma o volante" and b["sub"] == ""  # the alert below says why
  # an older comma without the status: any alert text is at least amber, never green
  assert run("band", live(alert="TAKE CONTROL IMMEDIATELY", alert_status=None), NOW)["cls"] == "warn"
  assert run("band", live(alert="Pre-Enable", alert_status="normal"), NOW)["cls"] == "op"


def test_no_connection_or_old_data_says_nothing_about_who_drives():
  for link in ({"online": False, "age": 500, "now": T0}, {"online": True, "age": 5000, "now": T0}):
    b = run("band", live(), link)
    assert b["cls"] == "none" and b["who"] == "sem dados" and b["stale"] and b["acc"] is None
  assert run("band", None, NOW)["cls"] == "none"


def test_you_driving_and_why_it_does_not_engage():
  b = run("band", live(steering=False, engaged=False, why=["pedal pisado"]), NOW)
  assert b["cls"] == "you" and b["who"] == "você dirigindo" and b["sub"] == "não engata: {motivos}" and b["why"] == ["pedal pisado"]
  assert run("band", {"driving": False}, NOW)["who"] == "carro desligado"


def test_the_band_says_what_the_driver_took():
  assert run("band", live(acc_active=True, v=60, gas=True), NOW)["sub"] == "você no acelerador"
  assert run("band", live(acc_active=True, v=60, brake=True), NOW)["sub"] == "você no freio"
  assert run("band", live(acc_active=True, v=60, driver=True), NOW)["sub"] == "você no volante"
  assert run("band", live(acc_active=True, v=60, brake=True, gas=True), NOW)["sub"] == "você no freio"
  assert run("band", live(acc_active=True, v=60), NOW)["sub"] == "volante ativo"
  assert run("band", live(steering=False, gas=True), NOW)["sub"] == "openpilot desligado"  # nothing to take from


# ----- the comma card, car off

HEALTH = {"device": {"temp": 48, "thermal": "ok", "free": 57}, "down": [], "learned": {}, "commit": "abc1234"}


def test_comma_ok_says_what_it_checked_and_never_that_the_car_is_ready():
  c = run("commaChecks", True, HEALTH)
  assert c["title"] == "Comma ok" and c["cls"] == "ok" and "câmeras" in c["sub"].lower()
  assert "pronto" not in json.dumps(c, ensure_ascii=False).lower()
  info = [i for i in c["items"] if i["ok"] is None]
  assert {i["text"] for i in info} == {"Versão {versao}", "Aprendizado aparece depois de dirigir"}  # not checks


def test_comma_temperature_follows_the_status_of_the_comma():
  for thermal, ok in (("ok", True), ("overheated", False), ("critical", False)):
    c = run("commaChecks", True, {**HEALTH, "device": {"temp": 69, "thermal": thermal, "free": 57}})
    assert next(i["ok"] for i in c["items"] if "{graus}" in i["text"]) is ok


def test_comma_problems():
  c = run("commaChecks", True, {**HEALTH, "down": ["modeld"], "device": {"temp": 80, "thermal": "overheated", "free": 4}})
  assert c["title"] == "Atenção no comma" and c["cls"] == "att"
  assert [i["ok"] for i in c["items"] if i["ok"] is not None] == [False, False, False]
  assert run("commaChecks", False, HEALTH)["title"] == "Sem dados do comma"
  assert run("commaChecks", True, None)["items"] == []


# ----- lane text

def test_centimeters_only_with_both_lines():
  assert run("laneText", {"left": -1.2, "right": 1.86, "left_ok": True, "right_ok": True}) == {"text": "Na faixa · {cm} cm à esquerda", "vars": {"cm": 33}}
  assert run("laneText", {"left": -1.2, "right": 1.86, "left_ok": True, "right_ok": False})["text"] == "Só a linha da esquerda está visível"
  assert run("laneText", {"left": -1.5, "right": 1.52, "left_ok": True, "right_ok": True})["text"] == "No centro da faixa"


def test_each_option_says_when_it_applies():
  assert run("applies", {"restart_needed": True}) == "Vale na próxima partida do carro."
  assert run("applies", {"restart_needed": False}) == "Vale no próximo engate."


# ----- speed bump

def test_bump_offers_to_keep_the_speed_it_was_passed_at():
  c = run("speedChoices", "lombada", 22, None)
  assert [x["v"] for x in c] == [None, 22, 10, 15, 20, 25, 30, 40, 50] and c[1]["label"] == "Manter {n}"
  assert [x["v"] for x in run("speedChoices", "lombada", 90, 35)] == [None, 10, 15, 20, 25, 30, 35, 40, 50]  # 90 is not a bump speed
  assert [x["v"] for x in run("speedChoices", "radar", 50, None)][-1] == 120
  assert run("keptSpeed", "lombada", 22) == 22 and run("keptSpeed", "lombada", 3) is None and run("keptSpeed", "radar", 22) is None


ON = {"alert": True, "slowdown": False, "long": False, "calibrated": False}


def test_bump_message_by_speed_never_says_the_car_slows_where_nothing_does():
  assert run("bumpMessage", 40, ON) == "Hoje só avisa. Ligue reduzir na lombada em Ajustes."
  assert run("bumpMessage", 30, {**ON, "slowdown": True}) == "Em teste: o comma toca o − do ACC até essa velocidade."
  assert run("bumpMessage", 20, ON).startswith("O ACC da Haval não vai abaixo de 30 km/h.")
  assert run("bumpMessage", 20, {**ON, "slowdown": True}).startswith("O ACC da Haval não vai abaixo de 30 km/h.")
  assert run("bumpMessage", 20, {**ON, "long": True}).startswith("O longitudinal do comma ainda não foi testado")
  assert run("bumpMessage", 20, {**ON, "alert": False}).startswith("Com o aviso de radar e lombada desligado")
  assert run("bumpMessage", None, ON) == ""


# ----- the passenger question

def test_passenger_question():
  parked, moving, off = {"on": True, "moving": False}, {"on": True, "moving": True}, {"on": False, "moving": False}
  assert run("passenger", {"ok": False}, parked) == {"ok": False, "ask": False}  # parked: never asks
  assert run("passenger", {"ok": False}, moving) == {"ok": False, "ask": True}   # moving, not confirmed
  assert run("passenger", {"ok": True}, moving) == {"ok": True, "ask": False}    # moving, confirmed
  assert run("passenger", {"ok": True}, parked)["ok"] is True                    # stopping in traffic keeps it
  gone = run("passenger", {"ok": True}, off)                                     # turning the car off ends it
  assert gone == {"ok": False, "ask": False}
  assert run("passenger", gone, moving)["ask"] is True                           # next trip asks once again


# ----- the fixed address

def test_fixed_address_plan():
  assert run("addressPlan", "lizpilot.local", False, True) == "fixed"
  assert run("addressPlan", "LizPilot.local", True, False) == "fixed"
  assert run("addressPlan", "192.168.0.23", True, False) == "move"   # not paired: go there before the code
  assert run("addressPlan", "192.168.0.23", True, True) == "offer"   # paired by address: suggest, pairs again once
  assert run("addressPlan", "192.168.0.23", False, True) == "ip"     # the name doesn't answer here


# ----- who had the wheel

SUMMARY = {"moving_s": 31 * 60, "op_s": 23 * 60, "help_s": 3 * 60, "you_s": 5 * 60, "stop_s": 6 * 60,
           "op_km": 23.4, "help_km": 2.1, "you_km": 3.5, "km": 29.0, "acc_s": 27 * 60}


def test_trip_split_in_minutes_km_and_shares():
  t = run("tripSplit", SUMMARY)
  assert [(p["key"], p["min"], p["km"]) for p in t["parts"]] == [("op", 23, 23.4), ("help", 3, 2.1), ("you", 5, 3.5)]
  assert abs(sum(p["pct"] for p in t["parts"]) - 100) < .01
  assert t["big"] == {"text": "{m} min", "vars": {"m": 23}} and t["line"] == "de volante por conta do openpilot"
  assert t["sub"]["vars"] == {"ajudou": 3, "dirigiu": 5, "parado": 6} and "Parado" in t["sub"]["text"]
  assert t["accPct"] == 87 and t["short"]["vars"] == {"op": 23, "andando": 31}


def test_trip_split_when_you_drove_and_without_data():
  t = run("tripSplit", {**SUMMARY, "op_s": 10, "help_s": 0, "you_s": 31 * 60, "stop_s": 0, "acc_s": 0})
  assert t["line"] == "você dirigiu nesta viagem" and t["big"]["vars"] == {"m": 31} and t["accPct"] is None
  assert "Parado" not in t["sub"]["text"]
  assert run("tripSplit", None) is None and run("tripSplit", {"moving_s": 0}) is None


def test_week_total_is_a_sum_of_the_last_7_days():
  now = 1_800_000_000
  trips = [{"mtime": now - 3600, "summary": SUMMARY}, {"mtime": now - 86400, "summary": SUMMARY},
           {"mtime": now - 8 * 86400, "summary": SUMMARY}, {"mtime": now - 60, "summary": None}]
  w = run("weekTotal", trips, now)
  assert w["trips"] == 2 and abs(w["km"] - 58.0) < .01 and w["op"] == {"text": "{m} min", "vars": {"m": 46}}
  assert run("duration", 130) == {"text": "{h} h {m} min", "vars": {"h": 2, "m": 10}}
  assert run("weekTotal", [], now) is None


def test_week_shadow_adds_up_drives_that_have_it():
  now = 1_800_000_000
  sh = {"sh_on_s": 600., "sh_hard": 2, "sh_earlier": 1, "sh_weaker": 1, "sh_fcw": 0, "sh_gap_s": 300., "sh_gap_t": 200.}
  trips = [{"mtime": now - 3600, "summary": {**SUMMARY, **sh}}, {"mtime": now - 7200, "summary": {**SUMMARY, **sh, "sh_gap_s": 150., "sh_gap_t": 100.}},
           {"mtime": now - 60, "summary": SUMMARY}, {"mtime": now - 9 * 86400, "summary": {**SUMMARY, **sh}}]
  w = run("weekShadow", trips, now)
  assert (w["hard"], w["earlier"], w["weaker"], w["trips"], w["minutes"]) == (4, 2, 2, 2, 20)
  assert abs(w["gap"] - 1.5) < 1e-9  # time-weighted: 450 / 300
  assert run("weekShadow", [{"mtime": now, "summary": SUMMARY}], now) is None  # older summaries don't have it
  assert run("gapMeters", 1.45, 60) == 24


def test_week_car_takes_maxima_minima_and_rates():
  now = 1_800_000_000
  a = {**SUMMARY, "km": 20., "se_grade_up": 8.2, "se_grade_down": 4., "se_v12_min": 12.3,
       "se_decel": 3.1, "se_lat": 2.4, "se_roll": 3., "se_wheel_s": 120., "se_wheel_t": 100.}
  b = {**SUMMARY, "km": 10., "se_grade_up": 11.5, "se_grade_down": 6.5, "se_v12_min": 12.6,
       "se_decel": 2.0, "se_lat": 1.0, "se_roll": 5., "se_wheel_s": 0., "se_wheel_t": 0.}
  w = run("weekCar", [{"mtime": now - 60, "summary": a}, {"mtime": now - 120, "summary": b}, {"mtime": now - 9 * 86400, "summary": a}], now)
  assert (w["up"], w["down"], w["v12"], w["decel"], w["roll"]) == (11.5, 6.5, 12.3, 3.1, 5)
  assert abs(w["wheels"] - 1.2) < 1e-9
  assert run("weekCar", [{"mtime": now, "summary": SUMMARY}], now) is None


# ----- Viagens: totals and versions (owner's decisions on the sweep, 02/11)

def test_totals_add_every_drive_with_a_summary_and_give_rates_from_1_km():
  trips = [{"mtime": 100, "summary": {"km": 30., "moving_s": 1800, "op_s": 1500, "off": 3, "alerts": 6}},
           {"mtime": 200, "summary": {"km": 20., "moving_s": 1200, "op_s": 600, "off": 2, "alerts": 0}},
           {"mtime": 300, "summary": None}]
  t = run("totals", trips, 0)
  assert t["trips"] == 2 and t["km"] == 50. and t["opPct"] == 70 and t["off"] == 5
  assert t["per100"] == {"off": 10., "alerts": 12.}
  assert run("totals", trips, 150)["trips"] == 1
  assert run("totals", [{"mtime": 1, "summary": {"km": .4, "moving_s": 30, "op_s": 0, "off": 1}}], 0)["per100"] is None
  assert run("totals", [], 0) is None


def test_versions_newest_first_without_the_unknown():
  trips = [{"summary": {"commit": "b2"}}, {"summary": {"commit": "a1"}}, {"summary": {"commit": "b2"}}, {"summary": {}}, {"summary": None}]
  assert run("versions", trips) == ["b2", "a1"]
  assert run("versionOf", {"summary": None}) == ""


# ----- Início: the whole history, the map (owner, 02/11 01:53) and hidden drives (02:02)

SAT = 1_793_448_000  # 2026-10-31 12:00 UTC, a Saturday


def drive(end, minutes=20, **summary):
  s = {"km": 12., "moving_s": 1000, "off": 0, "alerts": 0, **summary}
  return {"id": f"r{end}", "mtime": end, "minutes": minutes, "summary": s, "hidden": False}


def test_idle_drives_and_accents():
  assert run("idle", drive(SAT, km=.1, moving_s=20)) is True
  assert run("idle", drive(SAT, km=.1, moving_s=300)) is False  # a long time crawling is a drive
  assert run("idle", {"mtime": SAT, "minutes": 1, "summary": None}) is False  # not read yet: never hidden for it
  assert run("fold", "Sábado ÇÃO") == "sabado cao"


def test_history_search_finds_day_time_version_and_marks():
  a, b = drive(SAT, commit="abc1234"), drive(SAT - 86400 * 3, off=2, alerts=1)
  marks = [{"t": SAT - 300, "text": "Curva fechada"}]
  o = {"marks": marks, "locale": "pt-BR", "nowS": SAT + 3600, "words": {"today": "hoje", "yesterday": "ontem"}}

  def ids(q, **kw):
    return [t["id"] for t in run("history", [a, b], {**o, "query": q, **kw})["list"]]

  assert ids("") == [a["id"], b["id"]]
  assert ids("sabado") == [a["id"]] and ids("hoje") == [a["id"]] and ids("31/10") == [a["id"]]
  assert ids("ABC12") == [a["id"]] and ids("curva") == [a["id"]] and ids("curva quarta") == []  # every word must be found
  assert ids("quarta") == [b["id"]]
  h = run("history", [a, b], {**o, "filter": "desengates"})
  assert [t["id"] for t in h["list"]] == [b["id"]] and h["counts"] == {"todas": 2, "marcas": 1, "desengates": 1, "alertas": 1}
  assert ids("", filter="nada") == [a["id"], b["id"]]


def test_history_leaves_out_hidden_and_idle_drives_unless_asked():
  shown, hid, still = drive(SAT), {**drive(SAT - 600), "hidden": True}, drive(SAT - 1200, km=0., moving_s=0)
  h = run("history", [shown, hid, still], {})
  assert [t["id"] for t in h["list"]] == [shown["id"]] and (h["hidden"], h["idle"]) == (1, 1)
  assert len(run("history", [shown, hid, still], {"showHidden": True})["list"]) == 3


def test_history_by_day():
  days = run("byDay", [drive(SAT), drive(SAT - 3600, km=3.), drive(SAT - 86400)])
  assert [(d["key"], len(d["trips"]), d["km"], d["minutes"]) for d in days] == [("2026-10-31", 2, 15., 40), ("2026-10-30", 1, 12., 20)]


def test_map_fits_the_whole_path_and_lists_its_tiles():
  assert run("mercator", 0, 0, 1) == [256, 256]
  pts = [[1.0, 2.0, 0, 0], [1.01, 2.02, 3, 60], [1.005, 2.03, 1, 120]]
  f = run("mapFit", pts, 360, 220, 18)
  assert all(17.99 <= x <= 342.01 and 17.99 <= y <= 202.01 for x, y in f["xy"])  # the path touches the margin, never past it
  bigger = run("mapFit", pts, 720, 440, 18)
  assert bigger["z"] == f["z"] + 1  # twice the room, one zoom closer
  tiles = run("mapTiles", f, 360, 220)
  assert all(t["z"] == f["z"] for t in tiles) and min(t["left"] for t in tiles) <= 0 and max(t["left"] + t["size"] for t in tiles) >= 360
  xs, ys = [x for x, _ in f["xy"]], [y for _, y in f["xy"]]
  assert 256 <= f["size"] < 512 and max(max(xs) - min(xs), max(ys) - min(ys)) > 160  # fills the box
  assert run("mapFit", [[1.0, 2.0, 0, 0]], 360, 220, 18)["z"] == 17  # a single point: close in, not the world
  assert run("mapFit", [], 360, 220, 18) is None
  assert run("nearestAt", pts, 70) == 1


def test_trip_numbers_like_a_trip_computer():
  n = run("tripNumbers", {"km": 30., "moving_s": 1800, "stop_s": 300, "v_max": 98.4, "run_km": 12.34, "run_s": 600})
  assert (n["avg"], n["vmax"], n["runKm"], n["runMin"], n["movingMin"], n["stopMin"]) == (60, 98, 12.34, 10, 30, 5)
  old = run("tripNumbers", {"km": .5, "moving_s": 40})
  assert old["avg"] is None and old["vmax"] is None and old["runKm"] is None  # too short for an average; older summaries
  assert run("tripNumbers", None) is None


def test_weekly_trend_leaves_hidden_and_idle_drives_out():
  now = SAT  # a Saturday: the week started on Monday 26/10
  trips = [drive(now, km=20., moving_s=1000, op_s=800, off=1), drive(now - 3 * 86400, km=10., moving_s=1000, op_s=200),
           {**drive(now - 3600, km=50.), "hidden": True}, drive(now - 7200, km=0., moving_s=0), drive(now - 8 * 86400, km=40., moving_s=2000, op_s=1000, off=2)]
  w = run("weekly", trips, now, 3)
  assert [x["trips"] for x in w] == [0, 1, 2]
  assert w[2]["km"] == 30. and w[2]["opPct"] == 50 and abs(w[2]["off100"] - 100 / 30) < 1e-9
  assert w[1]["off100"] == 5. and w[0]["opPct"] is None


def summed(**kw):
  return {"id": "a", "mtime": 1000, "minutes": 10, "hidden": False,
          "summary": {"km": 10., "moving_s": 600., "op_s": 300., "off": 1, "alerts": 1, "sh_on_s": 60., "sh_hard": 1, "se_grade_up": 3.,
                      **kw.pop("summary", {})}, **kw}


def test_a_hidden_drive_leaves_the_sums_too():
  shown, hidden, idle = summed(id="a"), summed(id="b", hidden=True), summed(id="c", summary={"km": .1, "moving_s": 10.})
  trips = [shown, hidden, idle]
  assert [t["id"] for t in run("listed", trips)] == ["a"]
  assert run("weekTotal", trips, 1000)["trips"] == 1
  assert run("totals", trips, 0)["trips"] == 1
  assert run("weekShadow", trips, 1000)["trips"] == 1
  assert run("totals", [hidden], 0) is None


def test_a_row_finds_its_drive_in_the_list_of_now():
  # the list is replaced on each refresh: the row may hold the object of an older one
  old, new = {"id": "a", "hidden": False, "n": 1}, {"id": "a", "hidden": False, "n": 2}
  assert run("currentTrip", [new], old)["n"] == 2
  assert run("currentTrip", [], old)["n"] == 1


def test_the_newest_marks_win_over_the_list_of_drives():
  recent = [{"id": "1", "t": 10, "reason": "faixa"}]
  older = [{"id": "1", "t": 10, "reason": ""}, {"id": "2", "t": 5, "reason": "outro"}]
  got = run("mergeMarks", recent, older)
  assert sorted(m["id"] for m in got) == ["1", "2"] and next(m for m in got if m["id"] == "1")["reason"] == "faixa"
  assert run("mergeMarks", recent, None) == recent


def test_the_pages_finish_what_hiding_a_drive_starts():
  static = Path(__file__).parents[1] / "static"
  html, trip = (static / "index.html").read_text(), (static / "viagens.html").read_text()
  assert 'trip_hidden: "ocultou uma viagem"' in html and 'trip_shown: "mostrou uma viagem de novo"' in html
  assert "Todas as viagens estão ocultas." in trip and "sel.options.length" in trip  # with all of them hidden it stayed on loading
  assert "Desde sempre" not in html  # the sums only reach the drives whose logs are still on the comma


def test_a_hard_brake_shows_on_its_trip_page():
  trip = (Path(__file__).parents[1] / "static" / "viagens.html").read_text()
  assert 'const SHOWN = ["mark", "off", "hardbrake"];' in trip and 'e.kind !== "mark" && e.kind !== "off"' not in trip


def test_unpairing_leaves_no_list_of_drives_on_the_phone():
  html = (Path(__file__).parents[1] / "static" / "index.html").read_text()
  assert 'if (!v) { store("lpTrips", null); trips = null; }' in html


def test_no_path_yet_is_not_no_gps():
  # right after an update the phone has summaries from its cache and the comma is still reading the drives
  drive_read = {"summary": {"km": 5.}}
  assert run("pathSettled", drive_read, 0) is True
  assert run("pathSettled", drive_read, 12) is False
  assert run("pathSettled", {"summary": None}, 0) is False
