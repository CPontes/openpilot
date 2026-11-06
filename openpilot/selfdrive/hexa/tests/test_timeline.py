import json
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.hexa import timeline as tl

NS = int(1e9)


def write_rlog(path, seconds=8.0, bookmark_at=None, mark=None, oscillate=(2.0, 6.0), limit=(6.5, 7.5), pressed=None, acc=None, stop=None, speed=10.,
               lat_from=0.5, lat_off=None, gas=None, lanes=None, swing=8., bend=None, off_from=None):
  """A small rlog made of real cereal messages, as the comma would record it."""
  import zstandard
  from openpilot.cereal import log
  msgs = []
  t0 = 1_000 * NS

  def add(service, t, fill):
    ev = log.Event.new_message(logMonoTime=t, valid=True)
    fill(ev.init(service))
    msgs.append(ev.to_bytes())

  n = int(seconds * 100)
  for i in range(n):
    t = t0 + i * NS // 100
    s = i / 100
    osc = oscillate[0] <= s < oscillate[1]
    tq = (1 if (i // 25) % 2 else -1) * 0.6 if osc else (1.0 if limit[0] <= s < limit[1] else 0.1)

    def cs(c, s=s, osc=osc):
      c.vEgo = 0. if stop and stop[0] <= s < stop[1] else speed
      if pressed:
        c.steeringPressed = pressed[0] <= s < pressed[1]
      if gas:
        c.gasPressed = gas[0] <= s < gas[1]
      if acc:
        c.cruiseState.driving = acc[0] <= s < acc[1]
      c.steeringAngleDeg = (swing if (int(s * 4) % 2) else -swing) if osc else (bend[2] if bend and bend[0] <= s < bend[1] else 1.)
      c.steeringTorque = 150 if 1.0 <= s < 1.2 else 0
      c.cruiseState.speed = 20.
    add("carState", t, cs)
    lat_on = s >= lat_from and not (lat_off and lat_off[0] <= s < lat_off[1])  # the GWM drops latActive standing still
    add("carControl", t, lambda c, tq=tq, lat_on=lat_on: (setattr(c, "latActive", lat_on), setattr(c.actuators, "torque", tq)))
    add("carOutput", t, lambda c, tq=tq: setattr(c.actuatorsOutput, "torqueOutputCan", int(tq * 253)))
    on = s >= min(lat_from, 0.5) and not (off_from is not None and s >= off_from)
    add("selfdriveState", t, lambda c, s=s, on=on: (setattr(c, "enabled", on), setattr(c, "alertText1", "TAKE CONTROL" if 7.0 <= s < 7.5 else "")))
    if i % 5 == 0:
      def model(mv):
        for name, ys in (("laneLines", (-5.4, -1.8, 1.8, 5.4)), ("roadEdges", (-7., 7.))):
          lines = mv.init(name, len(ys))
          for line, y in zip(lines, ys, strict=True):
            line.x = [float(k * k) * 0.19 for k in range(33)]
            line.y = [y] * 33
        mv.position.x = [float(k * k) * 0.19 for k in range(33)]
        mv.position.y = [0.] * 33
        mv.laneLineProbs = [0.1, 0.9, 0.9, 0.1]
      add("modelV2", t, model)
      if lanes and i % 50 == 0:  # drivingModelData, as the qlog keeps it (2 Hz)
        add("drivingModelData", t, lambda d, s=s: (setattr(d.laneLineMeta, "leftProb", lanes(s)[0]), setattr(d.laneLineMeta, "rightProb", lanes(s)[1])))
      add("radarState", t, lambda r: (setattr(r.leadOne, "present", True), setattr(r.leadOne, "dRel", 25.), setattr(r.leadOne, "vRel", -1.)))
  if bookmark_at is not None:
    bt = t0 + int(bookmark_at * NS)
    add("userBookmark", bt, lambda b: None)
    if mark:
      ev = log.Event.new_message(logMonoTime=bt + NS // 2, valid=True)
      ev.logMessage = json.dumps({"msg": {"event": "hexa_mark", "mono_ns": bt, **mark}})
      msgs.append(ev.to_bytes())
  path.write_bytes(zstandard.ZstdCompressor().compress(b"".join(msgs)))
  return path


@pytest.fixture
def rlog(tmp_path):
  d = tmp_path / "00000001--abc--3"
  d.mkdir()
  return write_rlog(d / "rlog.zst", bookmark_at=4.0, mark={"reason": "balancou", "label": "balançou", "note": "teste"})


def test_frames_from_a_real_format_log(rlog):
  data = tl.build(tl.read_rlog(rlog))
  assert data["fps"] == 10 and 79 <= len(data["frames"]) <= 81
  assert data["xs"][0] == 0. and data["xs"][-1] == pytest.approx(76., abs=1)
  f = data["frames"][30]
  assert f["v"] == 36. and f["set"] == 72 and f["en"] and f["lat"]
  assert len(f["lanes"]) == 4 and len(f["lanes"][1]) == len(tl.MODEL_IDX) and f["lanes"][1][0] == -1.8
  assert f["lead"] == [25., -3.6] and f["lp"] == [0.1, 0.9, 0.9, 0.1]


def test_events_are_marked(rlog):
  data = tl.build(tl.read_rlog(rlog))
  kinds = [e["kind"] for e in data["events"]]
  assert "engage" in kinds and "oscillation" in kinds and "limit" in kinds and "mark" in kinds and "alert" in kinds
  mark = next(e for e in data["events"] if e["kind"] == "mark")
  assert mark["t"] == pytest.approx(4.0, abs=0.05) and mark["text"] == "bandeira: balançou — teste"
  limit = next(e for e in data["events"] if e["kind"] == "limit")
  assert limit["t"] == pytest.approx(6.5, abs=0.15) and limit["dur"] == pytest.approx(1.0, abs=0.15)
  osc = next(e for e in data["events"] if e["kind"] == "oscillation")
  assert osc["text"] == "balanço ±8.0°"
  assert data["summary"]["counts"]["mark"] == 1


def _frames(n, **over):
  base = {"t": 0., "en": True, "lat": True, "brk": False, "gas": False, "alert": "", "drv": 0, "tqc": 0.3, "tq": 0.3, "sat": False, "v": 30., "ang": 0.}
  return [dict(base, t=i / 10, **{k: v(i) if callable(v) else v for k, v in over.items()}) for i in range(n)]


def test_detect_debounces_driver_and_skips_bookmark_alert():
  frames = _frames(60, drv=lambda i: 150 if i in (5, 6, 10, 11, 50) else 0, alert=lambda i: "Bookmark Saved" if 20 <= i < 25 else "")
  ev = tl.detect(frames)
  drivers = [e for e in ev if e["kind"] == "driver"]
  assert [e["t"] for e in drivers] == [0.5, 5.0]  # 1.0 is the same push as 0.5
  assert drivers[0]["text"] == "você ajudou"
  assert not [e for e in ev if e["kind"] == "alert"]


def test_detect_disengage_with_brake():
  frames = _frames(20, en=lambda i: i < 10, brk=lambda i: i >= 10)
  ev = tl.detect(frames)
  assert {"t": 1.0, "kind": "disengage", "text": "desengatou (freio)"} in ev
  assert any(e["kind"] == "brake" and e["t"] == 1.0 for e in ev)


def test_short_limit_is_not_an_event():
  frames = _frames(30, tq=lambda i: 1.0 if 10 <= i < 13 else 0.3)
  assert not [e for e in tl.detect(frames) if e["kind"] == "limit"]


def test_segment_files(tmp_path, rlog):
  (tmp_path / "not-a-segment").mkdir()
  (tmp_path / "00000002--def--0").mkdir()  # no rlog yet
  segs = tl.segment_files(str(tmp_path))
  assert segs == [{"id": "00000001--abc--3", "file": str(rlog)}]
  assert tl.segment_files(str(tmp_path / "nope")) == []


def test_trip_segment_sums_the_minute(tmp_path):
  path = tmp_path / "qlog.zst"
  write_rlog(path, seconds=8.0, bookmark_at=3.0)
  d = tl.trip_segment(tl.read_rlog(path))
  s = d["summary"]
  assert abs(s["km"] - .08) < .002  # 8 s at 10 m/s
  assert abs(s["on_s"] / s["moving_s"] - 7.5 / 8) < .02
  assert s["marks"] == 1 and s["off"] == 0
  assert len(d["points"]) == 4 and all(p["lat"] is None for p in d["points"])
  assert abs(sum(p["step"] for p in d["points"]) - 74) < 21  # the last point's step stays for the next one


class Msg:
  def __init__(self, kind, t, **fields):
    self.kind, self.logMonoTime = kind, int(t * NS)
    setattr(self, kind, SimpleNamespace(**fields))

  def which(self):
    return self.kind


def drive(seconds, enabled=True, **pressed):
  """Ten messages per second: each pressed name is a list of (from, to) in seconds."""
  msgs = []
  for i in range(int(seconds * 10)):
    t = i / 10
    on = {k: any(a <= t < b for a, b in pressed.get(k, ())) for k in ("gas", "brake", "wheel")}
    msgs.append(Msg("selfdriveState", 1 + t, enabled=enabled))
    msgs.append(Msg("carControl", 1 + t, latActive=enabled))
    msgs.append(Msg("carState", 1 + t + .001, vEgo=10., gasPressed=on["gas"], brakePressed=on["brake"], steeringPressed=on["wheel"]))
  return tl.trip_segment(msgs)


def who(d, t):
  return next(p["who"] for p in d["points"] if p["t"] >= t)


def test_a_trip_tells_the_pedals_from_the_wheel():
  d = drive(12., brake=[(2.5, 3.)], gas=[(6.5, 7.)], wheel=[(9., 11.)])
  s = d["summary"]
  assert (s["brake"], s["gas"], s["driver"]) == (1, 1, 1)
  assert who(d, 4.) == "brake" and who(d, 8.) == "gas" and who(d, 10.) == "wheel" and who(d, 0.) == "op"
  assert {e["text"] for e in d["events"]} >= {"você no freio", "você no acelerador", "você no volante"}


def test_a_long_hold_paints_every_point_it_covers():
  d = drive(10., wheel=[(1., 9.)])
  assert [p["who"] for p in d["points"] if 2.5 <= p["t"] <= 8.5] == ["wheel"] * 3
  assert d["summary"]["driver"] == 1


def test_a_pedal_says_more_than_the_wheel_in_the_same_point():
  d = drive(6., wheel=[(2.2, 3.5)], gas=[(2.8, 3.2)])
  assert who(d, 4.) == "gas"


def test_a_short_touch_on_the_wheel_is_not_a_takeover():
  d = drive(6., wheel=[(2.2, 2.5)])
  assert d["summary"]["driver"] == 0 and who(d, 4.) == "op"


def test_a_pedal_tapped_twice_is_one_takeover():
  d = drive(8., gas=[(2., 2.2), (3., 3.2)])
  assert d["summary"]["gas"] == 1


def test_with_openpilot_off_it_is_all_you():
  d = drive(6., enabled=False, brake=[(2., 3.)], gas=[(4., 5.)])
  assert {p["who"] for p in d["points"]} == {"you"}
  assert (d["summary"]["brake"], d["summary"]["gas"]) == (0, 0)


def test_detect_marks_the_gas_only_when_engaged():
  base = {"v": 10, "en": True, "lat": True, "alert": "", "drv": 0, "tqc": 0., "tq": 0., "sat": False, "brk": False, "gas": False, "ang": 0., "press": False}
  frames = [{**base, "t": 0.}, {**base, "t": .1, "gas": True}, {**base, "t": .2}, {**base, "t": 5., "en": False}, {**base, "t": 5.1, "en": False, "gas": True}]
  assert [e["t"] for e in tl.detect(frames) if e["kind"] == "gas"] == [.1]


def test_trip_files_groups_by_route(tmp_path):
  for name, f in (("00000001--aaaa--0", "qlog.zst"), ("00000001--aaaa--2", "rlog.zst"), ("00000002--bbbb--0", "qlog.zst")):
    (tmp_path / name).mkdir()
    (tmp_path / name / f).write_bytes(b"x")
  (tmp_path / "boot").mkdir()
  trips = {t["id"]: t for t in tl.trip_files(str(tmp_path))}
  assert set(trips) == {"00000001--aaaa", "00000002--bbbb"}
  assert sorted(trips["00000001--aaaa"]["files"]) == [0, 2]


def test_trip_summary_splits_who_had_the_wheel(tmp_path):
  files = {}
  for n in (0, 1):
    write_rlog(tmp_path / f"{n}.zst", seconds=8.0, pressed=(2.0, 3.0), acc=(0.0, 4.0), stop=(6.0, 7.0))
    files[n] = str(tmp_path / f"{n}.zst")
  (tmp_path / "2.zst").write_bytes(b"broken")
  files[2] = str(tmp_path / "2.zst")
  s = tl.trip_summary(files)
  # per minute: you 0-0.5 s, openpilot 0.5-2 and 3-6 and 7-8, your hand 2-3, stopped 6-7
  assert abs(s["you_s"] - 1.0) < .1 and abs(s["help_s"] - 2.0) < .1 and abs(s["op_s"] - 11.0) < .1 and abs(s["stop_s"] - 2.0) < .1
  assert abs(s["op_s"] + s["help_s"] + s["you_s"] - s["moving_s"]) < .05
  assert abs(s["op_km"] - .11) < .005 and abs(s["acc_s"] - 8.0) < .1
  assert s["segments"] == 3 and s["unread"] == 1


def test_a_trip_counts_the_alerts_that_ask_something_and_reads_the_version():
  msgs = [Msg("initData", .5, version="0.11.3", gitCommit="7006d57abcdef", params=SimpleNamespace(entries=[]))]
  alerts = [(1., "", "normal"), (2., "Carro da frente saiu", "normal"), (3., "TAKE CONTROL", "critical"), (3.1, "TAKE CONTROL", "critical"),
            (4., "", "normal"), (5., "Steer Unavailable", "userPrompt"), (6., "Pay Attention", "userPrompt")]
  for t, text, status in alerts:
    msgs.append(Msg("selfdriveState", t, enabled=True, alertText1=text, alertStatus=status))
  msgs.append(Msg("carState", 1.001, vEgo=10., gasPressed=False, brakePressed=False, steeringPressed=False))
  d = tl.trip_segment(msgs)
  assert (d["summary"]["alerts"], d["summary"]["alerts_crit"]) == (3, 1)
  assert d["version"] == {"version": "0.11.3", "commit": "7006d57"}


def test_an_alert_left_over_from_the_minute_before_is_not_counted_again():
  msgs = [Msg("selfdriveState", 1., enabled=True, alertText1="TAKE CONTROL", alertStatus="critical"),
          Msg("selfdriveState", 1.1, enabled=True, alertText1="TAKE CONTROL", alertStatus="critical"),
          Msg("carState", 1.001, vEgo=10., gasPressed=False, brakePressed=False, steeringPressed=False)]
  assert tl.trip_segment(msgs)["summary"]["alerts"] == 0


def test_trip_summary_keeps_the_first_version(tmp_path):
  write_rlog(tmp_path / "0.zst", seconds=8.0)
  s = tl.trip_summary({0: str(tmp_path / "0.zst")})
  assert s["alerts"] >= 0 and s["version"] == "" and s["commit"] == ""  # this test log has no initData


def test_trip_summary_keeps_a_light_path_with_who_drove():
  import openpilot.selfdrive.hexa.timeline as t2
  segs = {0: [{"t": float(i * 2), "lat": 1.0 + i * 1e-4, "lon": 2.0, "who": "op" if i < 20 else "you"} for i in range(30)],  # made-up place
          1: [{"t": float(i * 2), "lat": None, "lon": None, "who": "op"} for i in range(30)]}
  real = t2.trip_segment, t2.read_rlog
  try:
    t2.read_rlog = lambda f: f
    t2.trip_segment = lambda seg, *_: {"summary": {}, "points": segs[seg], "events": [], "version": None}
    s = t2.trip_summary({0: 0, 1: 1})
  finally:
    t2.trip_segment, t2.read_rlog = real
  assert len(s["path"]) == 6 and s["path"][0][2] == 0 and s["path"][-1][2] == 3  # every 5th point, no point without GPS
  assert [p[3] for p in s["path"]] == [0, 10, 20, 30, 40, 50]


def test_trip_summary_has_the_numbers_anyone_wants(tmp_path):
  # top speed, and the longest stretch with no intervention: it goes on into the next minute, and the hand on the wheel ends it
  files = {}
  for n, kw in ((0, {"pressed": (3., 4.)}), (1, {"lat_from": 0.}), (2, {"pressed": (2., 3.), "lat_from": 0.})):
    d = tmp_path / f"00000001--abc--{n}"
    d.mkdir()
    files[n] = str(write_rlog(d / "rlog.zst", oscillate=(0., 0.), **kw))
  s = tl.trip_summary(files)
  assert s["v_max"] == 36
  assert abs(s["run_s"] - 14.5) < 0.6 and abs(s["run_km"] - 0.145) < 0.01  # 4 s + 8 s + 2.5 s: the hand counts from 0.5 s on
  del files[1]  # a minute that is missing breaks the stretch
  assert abs(tl.trip_summary(files)["run_s"] - 5) < 0.2  # the 4 s at the end of minute 0 no longer join the start of minute 2


def test_the_stretch_breaks_only_on_what_the_page_calls_an_intervention(tmp_path):
  # engaged at a stop (the GWM drops latActive standing still), a touch shorter than DRIVER_MIN_S: no break. A pedal: break
  d = tmp_path / "00000001--abc--0"
  d.mkdir()
  quiet = tl.trip_summary({0: str(write_rlog(d / "a.zst", seconds=8., oscillate=(0., 0.), lat_from=0., stop=(2., 5.), lat_off=(2., 5.),
                                              pressed=(6., 6.2)))})
  assert abs(quiet["run_s"] - 8) < 0.2
  pedal = tl.trip_summary({0: str(write_rlog(d / "b.zst", seconds=8., oscillate=(0., 0.), lat_from=0., gas=(3., 4.)))})
  assert abs(pedal["run_s"] - 4) < 0.2  # 0 to 3 s, then 4 to 8 s


def test_the_map_keeps_the_driver_over_the_whole_group():
  # a hand on the wheel in the middle of ~10 s still shows on the map; seconds count from minute 0 of the route
  pts = [{"t": 2. * i, "lat": 1. + i / 1e4, "lon": 2., "who": "wheel" if i == 2 else "op"} for i in range(10)]
  seg = {"points": pts, "events": [], "summary": {}, "version": {}}
  orig_read, orig_seg = tl.read_rlog, tl.trip_segment
  try:
    tl.read_rlog = lambda f: []
    tl.trip_segment = lambda events, *_: seg
    path = tl.trip_summary({3: "x"})["path"]
  finally:
    tl.read_rlog, tl.trip_segment = orig_read, orig_seg
  assert [p[2] for p in path] == [1, 0] and [p[3] for p in path] == [180, 190]


def test_a_prompt_with_the_text_of_a_softer_alert_is_counted():
  # driverDistracted1 and 2 say the same "Pay Attention": only the second asks for anything, and it has the sound
  msgs = [Msg("selfdriveState", 1., enabled=True, alertText1="", alertStatus="normal"),
          Msg("selfdriveState", 2., enabled=True, alertText1="Pay Attention", alertStatus="normal"),
          Msg("selfdriveState", 3., enabled=True, alertText1="Pay Attention", alertStatus="userPrompt"),
          Msg("selfdriveState", 3.1, enabled=True, alertText1="Pay Attention", alertStatus="userPrompt"),
          Msg("carState", 1.001, vEgo=10., gasPressed=False, brakePressed=False, steeringPressed=False)]
  assert tl.trip_segment(msgs)["summary"]["alerts"] == 1


def test_a_hard_brake_is_told_from_the_end_of_its_minute(monkeypatch):
  # the last minute of a drive is short: 20 s here, the brake at 15 s, so 5 s before the file closed
  msgs = []
  for i in range(200):
    t = i * .1
    msgs.append(Msg("carState", t, vEgo=15., aEgo=-7. if 15. <= t < 15.5 else 0., gasPressed=False, brakePressed=True, steeringPressed=False))
  monkeypatch.setattr(tl, "read_rlog", lambda path: msgs)
  found = tl.trip_summary({0: "x"})["auto_marks"]
  assert len(found) == 1 and found[0]["kind"] == "hardbrake" and 4. < found[0]["left"] < 5.


def test_the_kpis_wobble_lane_seen_and_where_it_let_go(tmp_path):
  # the wheel going back and forth only counts with openpilot steering on a straight (under 3° for 3 s); lane seen is both
  # lines at 0.5 or more; off_at in s from minute 0, only moving and not in the drive's last minute
  d = tmp_path / "00000001--abc--2"
  d.mkdir()
  calm = tl.trip_summary({2: str(write_rlog(d / "a.zst", seconds=10., oscillate=(0., 0.), lat_from=0., lanes=lambda s: (.9, .9)))})
  wavy = tl.trip_summary({2: str(write_rlog(d / "b.zst", seconds=10., oscillate=(0., 10.), swing=2., lat_from=0., lanes=lambda s: (.9, .2 if s < 5 else .9)))})
  curvy = tl.trip_summary({2: str(write_rlog(d / "e.zst", seconds=10., oscillate=(0., 0.), bend=(0., 10., 25.), lat_from=0.))})
  swerve = tl.trip_summary({2: str(write_rlog(d / "f.zst", seconds=10., oscillate=(0., 10.), swing=8., lat_from=0.))})
  def rms(s):
    return (s["wob_ss"] / s["wob_n"]) ** .5

  assert calm["wob_s"] > 6 and rms(calm) < .1
  assert rms(wavy) > 1.5  # ±2° every quarter second, on a straight
  assert curvy["wob_n"] == 0 and swerve["wob_n"] == 0  # a held curve, or swings past 3°, are not a straight
  assert calm["lane_n"] == wavy["lane_n"] > 10 and calm["lane_ok"] == calm["lane_n"]
  assert abs(wavy["lane_ok"] / wavy["lane_n"] - .5) < .1
  off = tl.trip_summary({2: str(write_rlog(d / "c.zst", seconds=8., oscillate=(0., 0.)))})
  assert off["off_at"] == [] and calm["off_at"] == []
  # letting go at 3 s of minute 0 moving is a pin; the same at the end of the drive (parking) or stopped is not
  let_go = {0: str(write_rlog(d / "g.zst", seconds=8., oscillate=(0., 0.), off_from=3.)), 2: str(write_rlog(d / "h.zst", seconds=8., oscillate=(0., 0.)))}
  assert tl.trip_summary(let_go)["off_at"] == [3]
  assert tl.trip_summary({2: str(write_rlog(d / "i.zst", seconds=8., oscillate=(0., 0.), off_from=3.))})["off_at"] == []
  stopped = {0: str(write_rlog(d / "j.zst", seconds=8., oscillate=(0., 0.), off_from=3., stop=(2., 5.))), 2: let_go[2]}
  assert tl.trip_summary(stopped)["off_at"] == []


def test_slow_traffic_counts_only_with_openpilot_steering_below_30(tmp_path):
  d = tmp_path / "00000001--abc--3"
  d.mkdir()
  slow = tl.trip_summary({3: str(write_rlog(d / "a.zst", seconds=8., lat_from=0., speed=5.))})
  fast = tl.trip_summary({3: str(write_rlog(d / "b.zst", seconds=8., lat_from=0.))})
  mine = tl.trip_summary({3: str(write_rlog(d / "c.zst", seconds=8., lat_from=99., speed=5.))})
  assert slow["slow_s"] > 7 and fast["slow_s"] == 0 and mine["slow_s"] == 0


def test_the_drive_summary_has_the_copilots_events(tmp_path):
  # the copilot's events read from the log (copilot_events.py), across minutes, for "A conferir" on the phone (owner, 03/11)
  d = tmp_path / "00000001--abc--0"
  d.mkdir()
  files = {0: str(write_rlog(d / "a.zst", seconds=8., oscillate=(0., 0.), lat_from=0., stop=(2., 5.), lat_off=(6.5, 9.)))}
  cop = tl.trip_summary(files)["cop"]
  assert [e["kind"] for e in cop] == ["stop_lead", "disengage"]  # stopped behind the car at 25 m; openpilot let go of the wheel at 36 km/h
  assert abs(cop[0]["s"] - 2) <= 1 and cop[0]["data"] == {"d": 25} and abs(cop[1]["s"] - 7) <= 1 and cop[1]["data"] == {"by": None}
  # a minute later the time counts from the drive's start
  d1 = tmp_path / "00000001--abc--1"
  d1.mkdir()
  files[1] = str(write_rlog(d1 / "a.zst", seconds=8., oscillate=(0., 0.), lat_from=0., stop=(2., 5.)))
  cop = tl.trip_summary(files)["cop"]
  assert [e["kind"] for e in cop] == ["stop_lead", "disengage", "stop_lead"] and abs(cop[2]["s"] - 62) <= 1


def test_the_drive_summary_knows_its_last_read_minute(tmp_path):
  # CTO review of app-12: the phone dates the log's events from the last minute the comma could read
  d = tmp_path / "00000001--abc--0"
  d.mkdir()
  files = {0: str(write_rlog(d / "a.zst", seconds=8.)), 1: str(tmp_path / "cut.zst")}
  (tmp_path / "cut.zst").write_bytes(b"not a log")
  s = tl.trip_summary(files)
  assert s["unread"] == 1 and s["end_seg"] == 0 and 7 <= s["end_s"] <= 8


def test_the_drive_summary_has_the_signals_seen_changing():  # MISS-01: the comma approval checks
  def minute(gears, door):
    msgs = []
    for i, g in enumerate(gears):
      msgs.append(Msg("carState", 1 + i, vEgo=0., gearShifter=g, doorOpen=door and i % 2 == 1, gasPressed=False, brakePressed=False,
                      steeringPressed=False))
    return msgs
  a = tl.trip_segment(minute(["park", "reverse"], True))["summary"]
  assert a["sg_gears"] == 3 and a["sg_door"] == 1
