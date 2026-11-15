"""The copilot's events read from a drive's log, after the drive, with the car off.

The same rules as the phone's live copilot (system/hexaweb/static/lp-logic.js, eventStep, with the CTO's review of
route 32), on one reading a second like the phone gets from /api/live, so what the phone missed with the screen off
still reaches "A conferir". tests/test_copilot_events.py runs both on the same readings: change one, change the other.

Nothing here runs with the car on: timeline.trip_summary reads the drive when the car is off.
"""
import math
from dataclasses import dataclass, field

STOP_KMH = 1  # lp-logic.js COP_STOP_KMH
LONG_S = 60  # COP_LONG_S
COOLDOWN_S = {"lead_brake": 15, "cut_in": 10, "disengage": 10}
# lp-logic.js, the same names
LEAD_BRAKE_KMH, LEAD_BRAKE_S, LEAD_BRAKE_MIN_KMH, LEAD_BRAKE_A, LEAD_BRAKE_NEAR_M, SAME_LEAD_M = 8, 3, 20, -0.4, 40, 6
CUT_IN_KMH, CUT_IN_M, CUT_IN_NEAR_M, CUT_IN_GAP_S = 15, 8, 30, 2
BLINKER_S, PULL_NEAR_M, PULL_ARMED_S, PULL_MOVE_KMH, PULL_GAP_M, GO_KMH, MOVED_KMH, DISENGAGE_KMH = 6, 20, 2, 4, 3, 3, 10, 8
EV_GAP_S = 3.5
SAMPLE_S = 1.  # one reading a second, like the phone
# per drive and kind, the newest (CTO review of app-12): route 32 had 125 events in 79 min, and a cap on the drive's first 40
# kept only its first 25 minutes. The review asks about the end of the drive, and a rare kind never loses to the stops
PER_KIND = 15


def _js_round1(x: float) -> float:
  return math.floor(x * 10 + .5) / 10  # Math.round(x * 10) / 10


@dataclass
class Lead:
  d: float  # m, rounded like /api/live
  v: float  # km/h, rounded
  a: float | None = None  # m/s², aLeadK rounded to 0.1 like /api/live


@dataclass
class Reading:
  v: float  # km/h, rounded like /api/live
  steering: bool  # openpilot on the wheel (latActive), as /api/live "steering"
  lead: Lead | None = None
  blinker: bool = False
  brake: bool = False
  gas: bool = False
  driver: bool = False


@dataclass
class CopilotEvents:
  events: list[dict] = field(default_factory=list)
  hist: list[dict] = field(default_factory=list)
  stop_at: float | None = None
  stop_lead: float | None = None
  long_done: bool = False
  moved: bool = False
  steer: bool | None = None
  lead: tuple[float, float] | None = None
  lead_at: float = 0.
  blink_at: float = -1e9
  last: dict = field(default_factory=dict)
  armed: dict | None = None
  wait_go: tuple[dict, float] | None = None  # (the pull_away event, since)
  cut: dict | None = None
  read_at: float | None = None
  prev: dict | None = None

  def _fresh(self) -> None:
    self.hist, self.stop_at, self.armed, self.wait_go, self.steer = [], None, None, None, None
    self.lead, self.prev, self.moved, self.cut = None, None, False, None

  def reset(self) -> None:
    """The car off, a gap or a minute that couldn't be read: every pending detection starts over (eventStep with no data)."""
    self._fresh()
    self.read_at = None

  def _fire(self, kind: str, t: float, v: float, data: dict, at: float | None = None) -> dict | None:
    if kind in self.last and t - self.last[kind] < COOLDOWN_S.get(kind, 0):
      return None
    self.last[kind] = t
    ev = {"s": round(at if at is not None else t, 1), "kind": kind, "v": v, "data": data}
    self.events.append(ev)
    same = [e for e in self.events if e["kind"] == kind]
    if len(same) > PER_KIND:
      self.events.remove(same[0])  # the oldest of its kind; a pull_away still waiting keeps its own dict
    return ev

  def step(self, t: float, r: Reading) -> None:
    v, lead = r.v, r.lead
    if self.read_at is not None and (t - self.read_at > EV_GAP_S or t < self.read_at):
      self._fresh()
    self.read_at = t
    if r.blinker:
      self.blink_at = t
    prev = self.prev if self.prev is not None and t - self.prev["t"] <= CUT_IN_GAP_S else None

    def closing(frm_v: float) -> float:
      return max(0., (v - frm_v) / 3.6) * (t - prev["t"])

    same_lead = bool(lead and prev and prev["lead"] and abs(prev["lead"]["d"] - closing(prev["lead"]["v"]) - lead.d) <= SAME_LEAD_M)
    if self.cut is not None:
      if lead and prev and abs(self.cut["d"] - closing(self.cut["v"]) - lead.d) <= SAME_LEAD_M:
        self._fire("cut_in", t, v, {"d": self.cut["d"]}, at=self.cut["t"])
        self.hist = []
      self.cut = None
    elif lead and prev and v >= CUT_IN_KMH and lead.d <= CUT_IN_NEAR_M and t - self.blink_at > BLINKER_S:
      blip = not prev["lead"] and self.lead is not None and t - self.lead_at <= 3 and abs(self.lead[0] - lead.d) < 5
      if (not prev["lead"] and not blip) or (prev["lead"] and prev["lead"]["d"] - closing(prev["lead"]["v"]) - lead.d >= CUT_IN_M):
        self.cut = {"t": t, "d": lead.d, "v": lead.v}
    self.prev = {"t": t, "lead": {"d": lead.d, "v": lead.v} if lead else None}
    self.hist = [h for h in self.hist if t - h["t"] <= LEAD_BRAKE_S]
    if lead and v >= LEAD_BRAKE_MIN_KMH:
      if not same_lead:
        self.hist = []
      self.hist.append({"t": t, "v": lead.v, "a": lead.a})
      top, n = max(h["v"] for h in self.hist), len(self.hist)
      decel = n >= 2 and all(isinstance(h["a"], int | float) and h["a"] <= LEAD_BRAKE_A for h in self.hist[-2:])
      if decel and lead.d <= LEAD_BRAKE_NEAR_M and top - lead.v >= LEAD_BRAKE_KMH and \
         self._fire("lead_brake", t, v, {"from": top, "to": lead.v, "d": lead.d}) is not None:
        self.hist = []
    else:
      self.hist = []
    if v < STOP_KMH or (self.stop_at is not None and v < GO_KMH):  # stopped, and still so at 1–2 km/h of creeping
      if self.stop_at is None:
        self.stop_at, self.stop_lead, self.long_done = t, (lead.d if lead else None), False
        if self.moved:
          self._fire("stop_lead" if lead else "stop_free", t, v, {"d": lead.d} if lead else {})
        self.moved = False
      if not self.long_done and t - self.stop_at >= LONG_S:
        self.long_done = True
        self._fire("stop_long", t, v, {"s": math.floor(t - self.stop_at + .5)})
      if lead and lead.d <= PULL_NEAR_M and self.wait_go is None:
        self.armed = {**self.armed, "d": min(self.armed["d"], lead.d), "miss": 0} if self.armed else {"at": t, "d": lead.d, "miss": 0}
      elif self.armed and not lead:
        self.armed["miss"] += 1
      if self.armed and t - self.armed["at"] >= PULL_ARMED_S and \
         (self.armed["miss"] >= 2 or (lead and (lead.v >= PULL_MOVE_KMH or lead.d >= self.armed["d"] + PULL_GAP_M))):
        ev = self._fire("pull_away", t, v, {"d": self.armed["d"]})
        self.armed = None
        if ev is not None:
          self.wait_go = (ev, t)
    elif self.stop_at is not None:
      # the car ahead and you left in the same reading: still its leaving, and you went right along
      if self.armed and self.wait_go is None and t - self.armed["at"] >= PULL_ARMED_S and \
         (not lead or lead.v >= PULL_MOVE_KMH or lead.d >= self.armed["d"] + PULL_GAP_M):
        self._fire("pull_away", t, v, {"d": self.armed["d"], "go_s": 0})
      elif self.wait_go is not None:
        ev, since = self.wait_go
        ev["data"]["go_s"] = _js_round1(t - since)
      elif not lead and self.stop_lead is None:
        self._fire("go_free", t, v, {})
      self.stop_at, self.armed, self.wait_go = None, None, None
    if v >= MOVED_KMH:
      self.moved = True
    if self.steer is True and not r.steering and v >= DISENGAGE_KMH:
      self._fire("disengage", t, v, {"by": "freio" if r.brake else "acelerador" if r.gas else "volante" if r.driver else None})
    self.steer = bool(r.steering)
    if lead:
      self.lead, self.lead_at = (lead.d, lead.v), t
