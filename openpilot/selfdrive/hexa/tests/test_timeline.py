import json

import pytest

from openpilot.selfdrive.hexa import timeline as tl

NS = int(1e9)


def write_rlog(path, seconds=8.0, bookmark_at=None, mark=None, oscillate=(2.0, 6.0), limit=(6.5, 7.5)):
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
      c.vEgo = 10.
      c.steeringAngleDeg = (8. if (int(s * 4) % 2) else -8.) if osc else 1.
      c.steeringTorque = 150 if 1.0 <= s < 1.2 else 0
      c.cruiseState.speed = 20.
    add("carState", t, cs)
    add("carControl", t, lambda c, tq=tq, s=s: (setattr(c, "latActive", s >= 0.5), setattr(c.actuators, "torque", tq)))
    add("carOutput", t, lambda c, tq=tq: setattr(c.actuatorsOutput, "torqueOutputCan", int(tq * 253)))
    add("selfdriveState", t, lambda c, s=s: (setattr(c, "enabled", s >= 0.5), setattr(c, "alertText1", "TAKE CONTROL" if 7.0 <= s < 7.5 else "")))
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
  base = {"t": 0., "en": True, "lat": True, "brk": False, "alert": "", "drv": 0, "tqc": 0.3, "tq": 0.3, "sat": False, "v": 30., "ang": 0.}
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


def test_trip_files_groups_by_route(tmp_path):
  for name, f in (("00000001--aaaa--0", "qlog.zst"), ("00000001--aaaa--2", "rlog.zst"), ("00000002--bbbb--0", "qlog.zst")):
    (tmp_path / name).mkdir()
    (tmp_path / name / f).write_bytes(b"x")
  (tmp_path / "boot").mkdir()
  trips = {t["id"]: t for t in tl.trip_files(str(tmp_path))}
  assert set(trips) == {"00000001--aaaa", "00000002--bbbb"}
  assert sorted(trips["00000001--aaaa"]["files"]) == [0, 2]
