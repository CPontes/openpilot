"""Hexa replay: turns a recorded log into frames for the phone viewer and a timeline of marked events.

Used by the phone panel on the comma (system/hexaweb, parked only) and by tools/hexa_replay.py on a
computer. Read only: nothing here touches the car.

Frame (10 Hz), short keys to keep the JSON small:
  t time since start (s) · v speed (km/h) · set ACC speed (km/h) · en engaged · lat steering active
  st selfdrive state · alert alert text · ang wheel angle (deg, + left) · tq applied torque (fraction of
  max, + left) · tqc requested torque (fraction, + left) · drv driver torque (raw, 100 = 1 Nm, + left)
  press driver steering · lad / laa desired / actual lateral accel (m/s², + right) · sat saturated
  brk / gas pedals
  bl / br blinkers · bsl / bsr blind spots · lanes, edges, path: y (m, + right) at XS
  lp lane line probabilities · lead [distance m, relative speed km/h] or null

Events: engage, disengage, alert, brake, limit (torque at the limit for 0.5 s or more), oscillation
(wheel swinging with the torque flipping sign), driver (driver steering, helping or against), mark
(bookmark, with the reason from the phone when there is one).
"""
import bz2
import json
from collections.abc import Iterable
from pathlib import Path

FPS = 10
STEER_MAX = 253
MODEL_IDX = (0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20)  # up to ~75 m ahead
LIMIT_MIN_S = 0.5
OSC_WINDOW_S = 3.0
OSC_MIN_FLIPS = 4
OSC_MIN_P2P_DEG = 6.0
DRIVER_TORQUE = 100  # raw units, 1 Nm
DRIVER_REPEAT_S = 3.0  # the same driver event again within this time is the same push
ALERT_REPEAT_S = 2.0
SKIP_ALERTS = ("Bookmark Saved",)  # already a mark event


def read_rlog(path) -> list:
  """Events of a local rlog/qlog (.zst, .bz2 or raw)."""
  from openpilot.cereal import log as capnp_log
  raw = Path(path).read_bytes()
  if str(path).endswith(".zst"):
    import zstandard
    raw = zstandard.ZstdDecompressor().stream_reader(raw).read()
  elif str(path).endswith(".bz2"):
    raw = bz2.decompress(raw)
  return list(capnp_log.Event.read_multiple_bytes(raw))


def _r(x, n=1):
  return round(float(x), n)


def _mark_reason(log_message: str) -> dict | None:
  try:
    d = json.loads(log_message)
  except (TypeError, ValueError):
    return None
  msg = d.get("msg") if isinstance(d, dict) else None
  if isinstance(msg, dict) and msg.get("event") == "hexa_mark" and msg.get("mono_ns") is not None:
    return msg
  return None


def build(events: Iterable) -> dict:
  """events: log messages in any order (which(), logMonoTime, fields)."""
  events = sorted(events, key=lambda m: m.logMonoTime)
  latest: dict = {}
  frames: list[dict] = []
  bookmarks: list[int] = []
  reasons: dict[int, dict] = {}
  xs: list[float] | None = None
  t0 = None
  next_frame = None
  step = int(1e9 / FPS)

  for m in events:
    w = m.which()
    if w == "userBookmark":
      bookmarks.append(m.logMonoTime)
      continue
    if w == "logMessage":
      r = _mark_reason(m.logMessage)
      if r is not None:
        reasons[int(r["mono_ns"])] = {**reasons.get(int(r["mono_ns"]), {}), **{k: v for k, v in r.items() if v is not None}}
      continue
    if w in ("carState", "carControl", "carOutput", "controlsState", "selfdriveState", "modelV2", "radarState"):
      latest[w] = getattr(m, w)
    if w == "modelV2" and xs is None:
      mx = list(m.modelV2.laneLines[0].x) if len(m.modelV2.laneLines) else []
      if len(mx) > max(MODEL_IDX):
        xs = [_r(mx[i]) for i in MODEL_IDX]
    if w != "carState":
      continue
    if t0 is None:
      t0 = m.logMonoTime
      next_frame = t0
    if m.logMonoTime >= next_frame:
      frames.append(_frame(latest, (m.logMonoTime - t0) / 1e9))
      next_frame += step
      while next_frame <= m.logMonoTime:
        next_frame += step

  t0 = t0 or 0
  marks = [((b - t0) / 1e9, reasons.get(b)) for b in bookmarks]
  timeline_events = detect(frames, marks)
  return {
    "fps": FPS,
    "xs": xs or [],
    "duration": frames[-1]["t"] if frames else 0.,
    "frames": frames,
    "events": timeline_events,
    "summary": summary(frames, timeline_events),
  }


def _model_lines(model) -> tuple[list, list, list, list]:
  def ys(xyz):
    y = list(xyz.y)
    return [_r(y[i]) for i in MODEL_IDX] if len(y) > max(MODEL_IDX) else []
  lanes = [ys(lane) for lane in model.laneLines]
  edges = [ys(edge) for edge in model.roadEdges]
  path = ys(model.position)
  probs = [_r(p, 2) for p in model.laneLineProbs]
  return lanes, edges, path, probs


def _frame(latest: dict, t: float) -> dict:
  cs = latest["carState"]
  f = {
    "t": _r(t, 2), "v": _r(cs.vEgo * 3.6), "ang": _r(cs.steeringAngleDeg), "drv": int(cs.steeringTorque),
    "press": bool(cs.steeringPressed), "brk": bool(cs.brakePressed), "gas": bool(cs.gasPressed),
    "bl": bool(cs.leftBlinker), "br": bool(cs.rightBlinker), "bsl": bool(cs.leftBlindspot), "bsr": bool(cs.rightBlindspot),
    "set": int(round(cs.cruiseState.speed * 3.6)) if cs.cruiseState.speed > 0 else None,
  }
  ss = latest.get("selfdriveState")
  f.update({"en": bool(ss.enabled), "st": str(ss.state), "alert": ss.alertText1} if ss else {"en": False, "st": "", "alert": ""})
  cc = latest.get("carControl")
  f.update({"lat": bool(cc.latActive), "tqc": _r(cc.actuators.torque, 2)} if cc else {"lat": False, "tqc": 0.})
  co = latest.get("carOutput")
  f["tq"] = _r(co.actuatorsOutput.torqueOutputCan / STEER_MAX, 2) if co else 0.
  ctl = latest.get("controlsState")
  if ctl is not None and ctl.lateralControlState.which() == "torqueState":
    ts = ctl.lateralControlState.torqueState
    f.update({"lad": _r(ts.desiredLateralAccel, 2), "laa": _r(ts.actualLateralAccel, 2), "sat": bool(ts.saturated)})
  else:
    f.update({"lad": 0., "laa": 0., "sat": False})
  model = latest.get("modelV2")
  if model is not None:
    f["lanes"], f["edges"], f["path"], f["lp"] = _model_lines(model)
  rs = latest.get("radarState")
  f["lead"] = [_r(rs.leadOne.dRel), _r(rs.leadOne.vRel * 3.6)] if rs is not None and rs.leadOne.present else None
  return f


def detect(frames: list[dict], marks: list[tuple[float, dict | None]] | None = None) -> list[dict]:
  ev: list[dict] = []
  prev = None
  limit_start = None
  last_alert: dict[str, float] = {}
  last_driver: dict[str, float] = {}
  for f in frames:
    t = f["t"]
    if prev is not None:
      if f["en"] and not prev["en"]:
        ev.append({"t": t, "kind": "engage", "text": "engatou"})
      if prev["en"] and not f["en"]:
        ev.append({"t": t, "kind": "disengage", "text": "desengatou" + (" (freio)" if f["brk"] or prev["brk"] else "")})
      if f["brk"] and not prev["brk"] and (f["en"] or prev["en"]):
        ev.append({"t": t, "kind": "brake", "text": "freio"})
      if f["alert"] and f["alert"] != prev["alert"] and f["alert"] not in SKIP_ALERTS \
         and t - last_alert.get(f["alert"], -1e9) > ALERT_REPEAT_S:
        ev.append({"t": t, "kind": "alert", "text": f["alert"]})
        last_alert[f["alert"]] = t
      if f["lat"] and abs(f["drv"]) >= DRIVER_TORQUE and abs(prev["drv"]) < DRIVER_TORQUE:
        text = "você ajudou" if f["drv"] * f["tqc"] > 0 else "você foi contra"
        if t - last_driver.get(text, -1e9) > DRIVER_REPEAT_S:
          ev.append({"t": t, "kind": "driver", "text": text})
        last_driver[text] = t
    at_limit = f["lat"] and (f["sat"] or abs(f["tq"]) >= 0.99)
    if at_limit and limit_start is None:
      limit_start = t
    elif not at_limit and limit_start is not None:
      if t - limit_start >= LIMIT_MIN_S:
        ev.append({"t": limit_start, "dur": _r(t - limit_start, 1), "kind": "limit", "text": "força no limite"})
      limit_start = None
    prev = f
  if limit_start is not None and frames and frames[-1]["t"] - limit_start >= LIMIT_MIN_S:
    ev.append({"t": limit_start, "dur": _r(frames[-1]["t"] - limit_start, 1), "kind": "limit", "text": "força no limite"})

  ev += _oscillations(frames)
  for t, reason in marks or []:
    text = f"bandeira: {reason['label']}" if reason and reason.get("label") else "bandeira sem motivo"
    if reason and reason.get("note"):
      text += f" — {reason['note']}"
    ev.append({"t": _r(t, 2), "kind": "mark", "text": text})
  return sorted(ev, key=lambda e: e["t"])


def _oscillations(frames: list[dict]) -> list[dict]:
  """Wheel swinging back and forth with openpilot steering: the torque flips sign at least
  OSC_MIN_FLIPS times in OSC_WINDOW_S and the wheel moves OSC_MIN_P2P_DEG or more, peak to peak."""
  n = max(int(OSC_WINDOW_S * FPS), 2)
  hits = []
  for i in range(n, len(frames) + 1):
    win = frames[i - n:i]
    if not all(f["lat"] and f["v"] > 1 for f in win):
      continue
    signs = [1 if f["tq"] > 0.05 else -1 if f["tq"] < -0.05 else 0 for f in win]
    signs = [s for s in signs if s]
    flips = sum(1 for a, b in zip(signs, signs[1:], strict=False) if a != b)
    p2p = max(f["ang"] for f in win) - min(f["ang"] for f in win)
    if flips >= OSC_MIN_FLIPS and p2p >= OSC_MIN_P2P_DEG:
      hits.append((win[0]["t"], win[-1]["t"], p2p))
  out = []
  for start, end, p2p in hits:
    if out and start <= out[-1]["t"] + out[-1]["dur"]:
      last = out[-1]
      last["dur"] = _r(max(last["t"] + last["dur"], end) - last["t"], 1)
      last["_p2p"] = max(last["_p2p"], p2p)
    else:
      out.append({"t": start, "dur": _r(end - start, 1), "kind": "oscillation", "_p2p": p2p})
  for e in out:
    e["text"] = f"balanço ±{_r(e.pop('_p2p') / 2)}°"
  return out


def summary(frames: list[dict], events: list[dict]) -> dict:
  if not frames:
    return {"lat_pct": 0, "v_max": 0, "counts": {}}
  counts: dict[str, int] = {}
  for e in events:
    counts[e["kind"]] = counts.get(e["kind"], 0) + 1
  return {
    "lat_pct": round(100 * sum(1 for f in frames if f["lat"]) / len(frames)),
    "v_max": max(f["v"] for f in frames),
    "counts": counts,
  }


def segment_files(log_root: str, limit: int = 200) -> list[dict]:
  """Recorded segments on the comma, newest first: [{"id": "<route>--<n>", "file": path}]."""
  root = Path(log_root)
  if not root.is_dir():
    return []
  out = []
  for d in root.iterdir():
    if not d.is_dir() or "--" not in d.name:
      continue
    for name in ("rlog.zst", "rlog", "rlog.bz2"):
      if (d / name).is_file():
        out.append({"id": d.name, "file": str(d / name), "mtime": (d / name).stat().st_mtime})
        break
  out.sort(key=lambda s: -s["mtime"])
  return [{"id": s["id"], "file": s["file"]} for s in out[:limit]]
