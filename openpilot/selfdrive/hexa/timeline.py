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

from openpilot.selfdrive.hexa import sensors as hexa_sensors
from openpilot.selfdrive.hexa.copilot_events import SAMPLE_S as COP_SAMPLE_S, CopilotEvents, Lead, Reading
from openpilot.selfdrive.hexa.shadow import KEYS as SHADOW_KEYS, ShadowCounter

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
TRIP_STEP_S = 2.0  # one point of the trip path
GPS_MAX_AGE_S = 3.0
DRIVER_MIN_S = 0.5  # on the wheel for at least this long counts as an intervention
SKIP_ALERTS = ("Bookmark Saved",)  # already a mark event
AUTO_TEXT = {"hardbrake": "freada brusca"}  # selfdrive/hexa/sensors.py: marks the comma makes by itself
AUTO_MARKS_MAX = 5  # a drive with more is noise
PATH_EVERY = 5  # one point of the map every 5 path points (~10 s): a drive of an hour is ~360 points
PATH_WHO = {"op": 0, "wheel": 1, "gas": 2, "brake": 2, "you": 3}  # the map's colors: openpilot, your hand, a pedal, you. The path's
# seconds count from minute 0 of the route
ALERT_COUNTED = ("userPrompt", "critical")  # an alert that asks something of the driver; "normal" ones only inform
TOOK_RANK = {None: 0, "wheel": 1, "gas": 2, "brake": 3}  # a path point covers 2 s: a pedal says more than a hand on the wheel
# who had the wheel while moving: openpilot alone, openpilot with the driver's hand on it, the driver alone
SPLIT_KEYS = ("op_s", "help_s", "you_s", "stop_s", "op_km", "help_km", "you_km", "acc_s")
# the panel's KPIs (owner, 02/11 14:54), summed minute by minute so a drive and a month add up the same way:
#   wob_ss, wob_n  the wheel going back and forth: steering angle around its own 1 s average, openpilot steering above 20 km/h
#                  (RMS = sqrt(ss / n), degrees). [hipótese] at the qlog's 10 Hz it tells the same as the 100 Hz measured on 02/11
#   lane_n, lane_ok  drivingModelData samples, openpilot steering above 20 km/h, and how many saw both lines at 0.5 or more
KPI_KEYS = ("wob_ss", "wob_n", "wob_s", "lane_n", "lane_ok", "slow_s")  # slow_s: openpilot steering below SLOW_MAX (the slow-traffic mission)
SLOW_MAX = 30 / 3.6
WOBBLE_MIN_SPEED = 20 / 3.6
WOBBLE_WINDOW_S = 1.
# [teste] CTO review of 05, 6 real routes: with curves in, 98% of the wobble was the curve itself and the route rated 9 came out
# worse than the unusable one. Only straights count: the angle under STRAIGHT_DEG for the last STRAIGHT_S.
STRAIGHT_DEG, STRAIGHT_S = 3., 3.
PIN_MIN_SPEED = 2.  # m/s: openpilot letting go while moving. Parked or stopped in traffic is no pin
PIN_END_S = 60.  # the last minute of a drive is parking: no pin there
LANE_OK = 0.5
OFF_AT_MAX = 50


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
      if f["gas"] and not prev["gas"] and f["en"]:
        if t - last_driver.get("gas", -1e9) > DRIVER_REPEAT_S:
          ev.append({"t": t, "kind": "gas", "text": "acelerador"})
        last_driver["gas"] = t
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


def trip_files(log_root: str, limit: int = 60) -> list[dict]:
  """Recorded drives on the comma, newest first: [{"id": route, "mtime": s, "files": {segment: path}}]."""
  root = Path(log_root)
  if not root.is_dir():
    return []
  trips: dict[str, dict] = {}
  for d in root.iterdir():
    route, sep, seg = d.name.rpartition("--")
    if not sep or not seg.isdigit() or not d.is_dir():
      continue
    for name in ("qlog.zst", "qlog", "qlog.bz2", "rlog.zst", "rlog", "rlog.bz2"):
      f = d / name
      if f.is_file():
        trip = trips.setdefault(route, {"id": route, "mtime": 0., "files": {}})
        trip["files"][int(seg)] = str(f)
        trip["mtime"] = max(trip["mtime"], f.stat().st_mtime)
        break
  return sorted(trips.values(), key=lambda t: -t["mtime"])[:limit]


def _op_long(messages: list) -> bool:
  """openpilot on the pedals in this minute. carParams can miss a short minute, initData never does."""
  for m in messages:
    w = m.which()
    if w == "carParams":
      return bool(m.carParams.openpilotLongitudinalControl)
    if w == "initData" and any(p.key == "AlphaLongitudinalEnabled" and bytes(p.value) == b"1" for p in m.initData.params.entries):
      return True
  return False


def trip_segment(events: Iterable, cop: CopilotEvents | None = None, offset: float = 0.) -> dict:
  """One minute of a drive for the trip page, from a qlog: much lighter than the replay.

  Each path point has the GPS fix and also the step from the wheels and the steering, because the GPS can be missing.
  cop: the copilot's events (copilot_events.py), fed one reading a second at offset + t; the drive's summary passes one
  across its minutes.
  """
  points: list[dict] = []
  out: list[dict] = []
  t0 = last = None
  next_point = 0.
  en = lat = brake = gas = False
  curvature = 0.
  step = dpsi = 0.
  fix = None
  fix_t = -1e9
  speed = 0.
  total = {"s": 0., "km": 0., "moving_s": 0., "on_s": 0., "driver": 0, "gas": 0, "brake": 0, "off": 0, "marks": 0, "alerts": 0, "alerts_crit": 0,
           **dict.fromkeys(SPLIT_KEYS, 0.)}
  alert_text: str | None = None  # the alert on screen now; None until the minute's first message, which may carry one from the minute before
  alert_status = "normal"
  version: dict | None = None
  driver_since = None
  driver_told = False
  took = None  # what the driver took from openpilot since the last path point
  pedal_at = {"gas": -1e9, "brake": -1e9}
  shadow = ShadowCounter()
  car = hexa_sensors.CarSensors()
  # the stretch with no intervention, now: openpilot engaged (stops included: the GWM drops latActive standing still) and
  # none of what the page counts as an intervention: a pedal, or the hand on the wheel for DRIVER_MIN_S
  run = [0., 0.]  # km and s
  run_head: list | None = None  # the run the minute started with, until its first break
  run_best = [0., 0.]
  run_hand = None  # since when the wheel is pressed
  v_max = 0.
  kpi = dict.fromkeys(KPI_KEYS, 0.)
  angles: list[tuple[float, float]] = []  # the last STRAIGHT_S of steering angle, for the wobble
  en_seen = False  # the minute's first carState can come before its first selfdriveState: not a break
  cop_lead: Lead | None = None
  cop_radar = cop_lat = False  # the minute starts with no state: the copilot reads only once it has the steering's and the radar's
  next_cop = 0.

  messages = sorted(events, key=lambda m: m.logMonoTime)
  shadow.on_op_long(_op_long(messages))
  for m in messages:
    w = m.which()
    t = (m.logMonoTime - t0) / 1e9 if t0 is not None else 0.
    if w == "drivingModelData":
      if en and lat and last is not None and speed > WOBBLE_MIN_SPEED:
        meta = m.drivingModelData.laneLineMeta
        kpi["lane_n"] += 1
        kpi["lane_ok"] += min(meta.leftProb, meta.rightProb) >= LANE_OK
    elif w == "deviceMotion":
      car.on_motion(m.deviceMotion.angularVelocityDevice.z)
    elif w == "peripheralState":
      car.on_voltage(m.peripheralState.voltage / 1000.)
    elif w == "longitudinalPlan":
      shadow.on_plan(t, m.longitudinalPlan.aTarget, m.longitudinalPlan.fcw)
    elif w == "radarState":
      lead = m.radarState.leadOne
      shadow.on_lead(lead.present, lead.dRel)
      cop_lead = Lead(round(lead.dRel), round(lead.vLead * 3.6), round(float(lead.aLeadK), 1)) if lead.present else None  # as /api/live
      cop_radar = True
    elif w == "userBookmark":
      out.append({"t": _r(t), "kind": "mark", "text": "bandeira", "mono_ns": m.logMonoTime})
      total["marks"] += 1
    elif w == "initData":
      if version is None:
        version = {"version": str(getattr(m.initData, "version", "") or ""), "commit": str(getattr(m.initData, "gitCommit", "") or "")[:7]}
    elif w == "selfdriveState":
      ss = m.selfdriveState
      enabled = ss.enabled
      shadow.on_selfdrive(enabled)
      text = str(getattr(ss, "alertText1", "") or "")
      status = str(getattr(ss, "alertStatus", "normal"))
      if alert_text is not None and text and status in ALERT_COUNTED and (text != alert_text or alert_status not in ALERT_COUNTED):
        total["alerts"] += 1
        total["alerts_crit"] += status == "critical"
      alert_text, alert_status = text, status
      if en and not enabled and t0 is not None:
        out.append({"t": _r(t), "kind": "off", "text": "desengatou no freio" if brake else "desengatou", "v": _r(speed)})
        total["off"] += 1
      en = enabled
      en_seen = True
    elif w == "carControl":
      lat = m.carControl.latActive
      cop_lat = True
      o = getattr(m.carControl, "orientationNED", ())
      if len(o) == 3:
        car.on_pose(t, o[0], o[1])
    elif w == "controlsState":
      curvature = m.controlsState.curvature
    elif w == "gpsLocationExternal":
      g = m.gpsLocationExternal
      if g.hasFix:
        fix, fix_t = (round(g.latitude, 6), round(g.longitude, 6)), t
    elif w == "carState":
      cs = m.carState
      if t0 is None:
        t0 = m.logMonoTime
        t = 0.
      dt = min(t - last, .5) if last is not None else 0.
      last = t
      v_max = max(v_max, cs.vEgo)
      speed = cs.vEgo
      angle = getattr(cs, "steeringAngleDeg", 0.)
      angles = [(ta, a) for ta, a in angles if t - ta < STRAIGHT_S] + [(t, angle)]
      straight = t - angles[0][0] >= STRAIGHT_S - .5 and all(abs(a) < STRAIGHT_DEG for _, a in angles)
      if en and lat and cs.vEgo > WOBBLE_MIN_SPEED and not cs.steeringPressed and straight:
        recent = [a for ta, a in angles if t - ta < WOBBLE_WINDOW_S]
        mean = sum(recent) / len(recent)
        kpi["wob_ss"] += (angle - mean) ** 2
        kpi["wob_n"] += 1
        kpi["wob_s"] += dt
      run_hand = (run_hand if run_hand is not None else t) if cs.steeringPressed else None
      if en and not (cs.gasPressed or cs.brakePressed or (run_hand is not None and t - run_hand >= DRIVER_MIN_S)):
        run[0] += cs.vEgo * dt / 1e3
        run[1] += dt
      elif en_seen:
        run_head = run_head if run_head is not None else list(run)
        run_best = max(run_best, run)
        run = [0., 0.]
      ws = getattr(cs, "wheelSpeeds", None)
      car.on_car(cs.vEgo, getattr(cs, "aEgo", 0.), getattr(cs, "steeringAngleDeg", 0.), (ws.fl, ws.fr, ws.rl, ws.rr) if ws is not None else None, dt, t)
      cruise = getattr(cs, "cruiseState", None)
      shadow.on_car(t, dt, cs.vEgo, getattr(cs, "aEgo", 0.), cs.brakePressed, getattr(cruise, "driving", False), getattr(cruise, "enabled", None))
      for pedal, pressed, was, text in (("brake", cs.brakePressed, brake, "você no freio"), ("gas", cs.gasPressed, gas, "você no acelerador")):
        if en and pressed:
          took = max(took, pedal, key=TOOK_RANK.__getitem__)
          if not was:
            if t - pedal_at[pedal] > DRIVER_REPEAT_S:
              out.append({"t": _r(t), "kind": pedal, "text": text})
              total[pedal] += 1
            pedal_at[pedal] = t
      if cop is not None and cop_lat and cop_radar and t >= next_cop:  # one reading a second, like the phone's /api/live
        cop.step(offset + t, Reading(round(cs.vEgo * 3.6), bool(lat), cop_lead, bool(cs.leftBlinker or cs.rightBlinker),
                                     bool(cs.brakePressed), bool(cs.gasPressed), bool(cs.steeringPressed)))
        next_cop = t + COP_SAMPLE_S
      brake, gas = cs.brakePressed, cs.gasPressed
      total["s"] += dt
      total["km"] += cs.vEgo * dt / 1e3
      step += cs.vEgo * dt
      dpsi -= curvature * cs.vEgo * dt  # curvature is positive to the right, the page turns counterclockwise
      if cs.vEgo > .5:
        total["moving_s"] += dt
        total["on_s"] += dt if lat else 0.
        part = "you" if not lat else ("help" if cs.steeringPressed else "op")
        total[part + "_s"] += dt
        total[part + "_km"] += cs.vEgo * dt / 1e3
        total["acc_s"] += dt if getattr(getattr(cs, "cruiseState", None), "driving", False) else 0.  # logs before the field: 0, shown only when > 0
        kpi["slow_s"] += dt if lat and cs.vEgo < SLOW_MAX else 0.
      else:
        total["stop_s"] += dt
      if en and cs.steeringPressed:
        driver_since = t if driver_since is None else driver_since
        if t - driver_since >= DRIVER_MIN_S:
          took = max(took, "wheel", key=TOOK_RANK.__getitem__)
          if not driver_told:
            out.append({"t": _r(driver_since), "kind": "driver", "text": "você no volante"})
            total["driver"] += 1
            driver_told = True
      elif not cs.steeringPressed:
        driver_since = None
        driver_told = False
      if t >= next_point:
        fresh = fix is not None and t - fix_t <= GPS_MAX_AGE_S
        points.append({"t": _r(t), "v": _r(cs.vEgo * 3.6), "on": bool(lat), "who": took or ("op" if lat else "you"), "step": _r(step), "dpsi": _r(dpsi, 4),
                       "lat": fix[0] if fresh else None, "lon": fix[1] if fresh else None})
        took = None
        step = dpsi = 0.
        next_point += TRIP_STEP_S

  for k in ("s", "km", "moving_s", "on_s", *SPLIT_KEYS):
    total[k] = _r(total[k], 3)
  total.update(shadow.result())
  total.update(car.result())
  total.update({k: _r(v, 3) for k, v in kpi.items()})
  # the longest stretch without an intervention; head and tail join with the minutes around it (trip_summary)
  total.update({"v_max": _r(v_max * 3.6, 1), "run_best": [_r(x, 3) for x in max(run_best, run)], "run_tail": [_r(x, 3) for x in run],
                "run_head": [_r(x, 3) for x in (run_head if run_head is not None else run)], "run_whole": run_head is None})
  for at, kind in car.events:  # automatic marks: a very hard brake
    out.append({"t": at, "kind": kind, "text": AUTO_TEXT[kind]})
  out.sort(key=lambda e: e["t"])
  return {"points": points, "events": out, "summary": total, "shadow": shadow.events, "version": version}


def trip_summary(files: dict[int, str], stop=lambda: False) -> dict | None:
  """The whole drive in numbers, for the trip list: who had the wheel, for how long and how far, minute by minute.
  A minute that can't be read is left out and counted, so a broken file doesn't lose the drive. A long drive takes a
  while: stop is asked before each minute, and None means it gave up (the car turned on)."""
  keys = ("s", "km", "moving_s", "on_s", "driver", "gas", "brake", "off", "marks", "alerts", "alerts_crit", *SPLIT_KEYS, *SHADOW_KEYS, *KPI_KEYS)
  total: dict = dict.fromkeys(keys, 0.)
  car: dict = {}
  version: dict = {}
  auto: list[dict] = []
  path: list[list] = []
  off_at: list[int] = []  # seconds from minute 0 where openpilot let go moving: the map's red pins
  end_s, end_seg = 0., None  # where the last read minute ended, and that minute (its file's time is the drive's clock)
  unread = 0
  run_best, run_cur, prev, v_max = [0., 0.], [0., 0.], None, 0.
  cop = CopilotEvents()  # the copilot's events, for "A conferir" on the phone (owner, 03/11)
  for seg in sorted(files):
    if stop():
      return None
    if prev is not None and seg != prev + 1:
      cop.reset()  # a gap: nothing pending carries over it
    try:
      seg_data = trip_segment(read_rlog(files[seg]), cop, seg * 60.)
    except Exception:
      unread += 1
      cop.reset()
      continue
    part = seg_data["summary"]
    version = version or seg_data.get("version") or {}
    # told from the end of the minute: a drive's last minute is short
    auto += [{"seg": seg, "left": _r(part["s"] - e["t"]), "kind": e["kind"]} for e in seg_data["events"] if e["kind"] in AUTO_TEXT]
    off_at += [round(seg * 60 + e["t"]) for e in seg_data["events"] if e["kind"] == "off" and e.get("v", 0.) >= PIN_MIN_SPEED]
    if seg * 60 + part.get("s", 0.) >= end_s:
      end_s, end_seg = seg * 60 + part.get("s", 0.), seg
    pts = seg_data["points"]
    for i in range(0, len(pts), PATH_EVERY):  # who drove: the most of the driver in the group (you > a pedal > the hand > openpilot)
      group = pts[i:i + PATH_EVERY]
      p = next((q for q in group if q.get("lat") is not None), None)
      if p is not None:
        path.append([p["lat"], p["lon"], max(PATH_WHO.get(q.get("who"), 3) for q in group), round(seg * 60 + p["t"])])
    for k in keys:
      total[k] += part.get(k, 0)
    hexa_sensors.merge(car, part)
    v_max = max(v_max, part.get("v_max", 0.))
    if "run_head" in part:  # a stretch crosses minutes only when they follow each other: an unread minute breaks it
      head, tail = part["run_head"], part["run_tail"]
      joined = [a + b for a, b in zip(run_cur if prev == seg - 1 else [0., 0.], head, strict=True)]
      run_cur = joined if part["run_whole"] else list(tail)
      run_best = max(run_best, joined, list(part["run_best"]), run_cur)
    prev = seg
  out = {k: _r(v, 2) for k, v in {**total, **car}.items()}
  for k in ("driver", "gas", "brake", "off", "marks", "alerts", "alerts_crit", "sh_hard", "sh_earlier", "sh_weaker", "sh_fcw", "wob_n", "lane_n", "lane_ok"):
    out[k] = int(total[k])
  if "se_hard_brakes" in out:
    out["se_hard_brakes"] = int(out["se_hard_brakes"])
  out.update({"v_max": round(v_max), "run_km": _r(run_best[0], 2), "run_s": round(run_best[1])})
  return {**out, "segments": len(files), "unread": unread, "version": version.get("version", ""), "commit": version.get("commit", ""),
          "auto_marks": auto[:AUTO_MARKS_MAX], "path": path, "cop": cop.events, "end_s": _r(end_s), "end_seg": end_seg,
          "off_at": [x for x in off_at if x < end_s - PIN_END_S][:OFF_AT_MAX]}
