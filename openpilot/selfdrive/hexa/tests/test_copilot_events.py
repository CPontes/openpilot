"""The copilot's events read from the log: the same as the phone's live copilot on the same readings."""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from openpilot.selfdrive.hexa.copilot_events import PER_KIND, CopilotEvents, Lead, Reading

LOGIC = Path(__file__).parents[3] / "system/hexaweb/static/lp-logic.js"
NODE = shutil.which("node") or next((p for p in ("/opt/node22/bin/node", "/usr/local/bin/node") if Path(p).exists()), None)


def car(v, lead=None, steering=True, **kw):
  return Reading(v, steering, Lead(*lead) if lead else None, **kw)


def run(samples):
  c = CopilotEvents()
  for t, r in samples:
    if r is None:
      c.reset()
    else:
      c.step(t, r)
  return c.events


def kinds(samples):
  return [e["kind"] for e in run(samples)]


def test_the_car_ahead_pulling_away_and_how_long_you_took():
  evs = run([(-12, car(40)), (-11, car(20)), *[(i, car(0, (6, 0))) for i in range(-10, 5)], (5, car(0, (7, 8))), (6, car(0, (10, 12))), (8, car(5, (14, 15)))])
  assert [e["kind"] for e in evs] == ["stop_lead", "pull_away"]
  assert evs[1]["s"] == 5 and evs[1]["data"] == {"d": 6, "go_s": 3.0}
  # it and you in the same reading: still its leaving, and you went right along
  same = run([(-11, car(40)), *[(i, car(0, (6, 0))) for i in range(-10, 5)], (5, car(6, (9, 10)))])
  assert [e["kind"] for e in same] == ["stop_lead", "pull_away"] and same[1]["data"] == {"d": 6, "go_s": 0}


def test_braking_ahead_needs_the_commas_deceleration_twice_close_and_the_same_car():
  follow = [(i, car(50, (30, 50, 0.))) for i in range(3)]
  assert kinds([*follow, (3, car(50, (28, 44, -1.2))), (4, car(48, (25, 40, -1.5)))]) == ["lead_brake"]
  assert kinds([*follow, (3, car(50, (28, 44, 0.))), (4, car(48, (25, 40, 0.)))]) == []  # its speed only: the radar's noise
  assert kinds([*[(i, car(50, (60, 50, 0.))) for i in range(3)], (3, car(50, (58, 44, -1.2))), (4, car(48, (55, 40, -1.5)))]) == []  # over 40 m


def test_cut_in_counts_from_the_first_reading_once_the_next_one_still_has_it():
  evs = run([(0, car(60)), (1, car(60)), (2, car(60, (18, 55))), (3, car(60, (17, 55)))])
  assert [e["kind"] for e in evs] == ["cut_in"] and evs[0]["s"] == 2
  assert kinds([(0, car(60)), (1, car(60)), (2, car(60, (18, 55))), (3, car(60))]) == []  # gone at the next reading
  assert kinds([(0, car(60, blinker=True)), (2, car(60)), (3, car(60, (18, 55))), (4, car(60, (17, 55)))]) == []  # your lane change


def test_stops_disengage_and_gaps():
  assert kinds([(0, car(40)), (1, car(0)), *[(1 + i, car(0)) for i in range(1, 62)], (63, car(5))]) == ["stop_free", "stop_long", "go_free"]
  assert run([(0, car(50)), (1, car(50, steering=False, brake=True))])[0]["data"] == {"by": "freio"}
  assert kinds([(0, car(40)), (1, None), (2, car(0))]) == []  # the car off starts over
  assert kinds([(0, car(40)), (5, car(0))]) == []  # a gap of 5 s: nothing late as now


def test_keeps_the_newest_of_each_kind():
  # CTO review of app-12: a cap on the drive's first 40 kept 25 of route 32's 79 minutes, and the stops pushed the rest out
  c = CopilotEvents()
  for i in range(200):
    c.step(i * 30., car(50))
    c.step(i * 30. + 1, car(50, steering=False))
  assert len(c.events) == PER_KIND and c.events[-1]["s"] == 199 * 30. + 1 and c.events[0]["s"] == (200 - PER_KIND) * 30. + 1
  t = 6001.
  for _ in range(40):  # the stops of a long drive in town
    for v in (40, 40, 0, 0, 0, 40):
      t += 1
      c.step(t, car(v))
  kinds_now = [e["kind"] for e in c.events]
  assert kinds_now.count("disengage") == PER_KIND and kinds_now.count("stop_free") == PER_KIND


def test_a_pull_away_waiting_still_gets_its_time_after_older_ones_go():
  c = CopilotEvents()
  t = 0.
  for _ in range(PER_KIND + 3):
    for r in [car(40)] + [car(0, (5, 0))] * 3 + [car(0, (9, 6))] + [car(0)] * 2 + [car(20)] + [car(40)] * 3:
      t += 1
      c.step(t, r)
  pulls = [e for e in c.events if e["kind"] == "pull_away"]
  assert len(pulls) == PER_KIND and all(e["data"].get("go_s") == 3. for e in pulls)


def _random_drive(seed: int, n: int = 1200) -> list:
  rnd = random.Random(seed)
  v, lead, out, steer, t = 0., None, [], True, 0.
  for _ in range(n):
    t += rnd.choice((1., 1., 1., 0.9, 1.1, 1.3, 4.))  # the page's readings are not exact, and sometimes it misses a few
    v = max(0., min(110., v + rnd.choice((-12, -6, -2, 0, 0, 2, 6, 8)) * (rnd.random() < .7)))
    if rnd.random() < .08:
      lead = None if lead else Lead(round(rnd.uniform(3, 60)), round(rnd.uniform(0, 100)), round(rnd.uniform(-3, 1), 1))
    elif lead:
      lead = Lead(max(1, lead.d + rnd.choice((-9, -3, -1, 0, 1, 3, 9))), max(0, lead.v + rnd.choice((-20, -9, -6, -2, 0, 2, 6))),
                  rnd.choice((None, round(rnd.uniform(-3, 1), 1), -0.4, -1.)))
    if rnd.random() < .03:
      steer = not steer
    out.append((round(t, 3), None if rnd.random() < .005 else Reading(round(v if rnd.random() > .2 else 0), steer, lead, rnd.random() < .03,
                                                                        rnd.random() < .05, rnd.random() < .02, rnd.random() < .05)))
  return out


def _phone(samples, tmp_path) -> list:
  readings = [None if r is None else [t, {"driving": True, "v": r.v, "steering": r.steering,
                                          "lead": {"d": r.lead.d, "v": r.lead.v, "a": r.lead.a} if r.lead else None,
                                          "blinker": "x" if r.blinker else "", "brake": r.brake, "gas": r.gas, "driver": r.driver}] for t, r in samples]
  f = tmp_path / "r.json"
  f.write_text(json.dumps(readings))
  script = f"""const L = require({json.dumps(str(LOGIC))}); let st = null; const out = [], idx = {{}};
  for (const x of JSON.parse(require("fs").readFileSync({json.dumps(str(f))}, "utf8"))) {{
    if (x === null) {{ st = L.eventStep(st, {{ driving: false }}, 0).st; continue; }}
    const r = L.eventStep(st, x[1], x[0] * 1000); st = r.st;
    for (const e of r.events) {{ idx[e.id] = out.length; out.push([Math.round(e.t / 100) / 10, e.kind, e.data]); }}
    for (const u of r.updates) if (idx[u.id] != null) Object.assign(out[idx[u.id]][2], u.data);
  }}
  process.stdout.write(JSON.stringify(out));"""
  return json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60, check=True).stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_the_same_events_as_the_phone_on_the_same_readings(seed, tmp_path):
  samples = _random_drive(seed)
  py = [[e["s"], e["kind"], e["data"]] for e in run(samples)]
  js = _phone(samples, tmp_path)
  assert len(py) > 5 and len({k for _, k, _ in py}) >= 4  # the drive does something
  keep, seen = [], {}
  for e in reversed(js):  # the comma keeps the newest PER_KIND of each kind, in their order
    seen[e[1]] = seen.get(e[1], 0) + 1
    if seen[e[1]] <= PER_KIND:
      keep.append(e)
  assert py == keep[::-1]
