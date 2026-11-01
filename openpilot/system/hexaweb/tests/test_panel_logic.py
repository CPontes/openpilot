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


# ----- the comma card, car off

HEALTH = {"device": {"temp": 48, "thermal": "green", "free": 57}, "down": [], "learned": {}, "commit": "abc1234"}


def test_comma_ok_says_what_it_checked_and_never_that_the_car_is_ready():
  c = run("commaChecks", True, HEALTH)
  assert c["title"] == "Comma ok" and c["cls"] == "ok" and "câmeras" in c["sub"].lower()
  assert "pronto" not in json.dumps(c, ensure_ascii=False).lower()
  info = [i for i in c["items"] if i["ok"] is None]
  assert {i["text"] for i in info} == {"Versão {versao}", "Aprendizado aparece depois de dirigir"}  # not checks


def test_comma_problems():
  c = run("commaChecks", True, {**HEALTH, "down": ["modeld"], "device": {"temp": 80, "thermal": "red", "free": 4}})
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
