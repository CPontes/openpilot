"""Shadow mode: what openpilot's own longitudinal would have done while the Haval ACC drove. Nothing changes in the car.

The planner runs with the stock ACC too, starting each step from the real speed and acceleration
(selfdrive/controls/lib/longitudinal_planner.py:58,76-79), so the logs already hold openpilot's opinion: longitudinalPlan
aTarget and its own FCW. This compares it with what the Haval did, only while openpilot was engaged, the Haval ACC was
driving and the car was moving, for LONG-02 (owner's request: safety and predictability at least as good as the Haval's):

- hard: Haval stops braking hard by itself, at or below HARD m/s², the driver off the brake.
- earlier: in those, openpilot asked for at least START m/s² of braking EARLY_S or more before the Haval started.
- weaker: in those, openpilot didn't start earlier and, at the Haval's hardest moment, asked for more than WEAKER m/s² less
  braking: it would have been late. The red flag.
- fcw: openpilot's own forward collision warning, rising edges.
- gap: time gap to the lead the Haval kept (gap_s / gap_t, a time-weighted mean).

Numbers to measure with, never a verdict by themselves: a hard brake from a cut-in is invisible to both before it.
"""
from collections import deque

HARD = -2.5  # m/s², the threshold of tools/analise/freadas_fortes.py
START = -1.0  # m/s²: a brake has started
END = -0.5  # m/s²: it's over
EARLY_S = 0.5
WINDOW_S = 6.  # how far back openpilot's plan is looked at before the Haval started braking
WEAKER = 1.5  # m/s²
PLAN_MAX_AGE_S = 1.  # the qlog keeps the plan at 2 Hz
MIN_GAP_SPEED = 3.  # m/s
KEYS = ("sh_hard", "sh_earlier", "sh_weaker", "sh_fcw", "sh_gap_s", "sh_gap_t", "sh_on_s")


class ShadowCounter:
  def __init__(self):
    self.totals = dict.fromkeys(KEYS, 0.)
    self.engaged = False
    self.plan_t = -1e9
    self.a_target = 0.
    self.fcw = False
    self.lead: tuple[bool, float] = (False, 0.)  # present, distance
    self.history: deque[tuple[float, float]] = deque()  # (t, aTarget) of the last WINDOW_S
    self.episode: dict | None = None  # the Haval braking now
    self.events: list[dict] = []  # every hard brake, for tools/analise/modo_sombra.py and a look at the video
    self.seen_driving = False  # logs from before cruiseState.driving (opendbc 8381d77) only have cruiseState.enabled
    self.op_long = False  # openpilot on the pedals: cruiseState.driving stays true and its braking would pass for the Haval's

  def on_op_long(self, op_long: bool) -> None:
    self.op_long = bool(op_long)

  def on_selfdrive(self, enabled: bool) -> None:
    self.engaged = bool(enabled)

  def on_plan(self, t: float, a_target: float, fcw: bool) -> None:
    if fcw and not self.fcw and self.engaged:
      self.totals["sh_fcw"] += 1
    self.plan_t, self.a_target, self.fcw = t, float(a_target), bool(fcw)
    self.history.append((t, self.a_target))
    while self.history and self.history[0][0] < t - WINDOW_S - 1.:
      self.history.popleft()

  def on_lead(self, present: bool, d_rel: float) -> None:
    self.lead = (bool(present), float(d_rel))

  def on_car(self, t: float, dt: float, v: float, a: float, brake: bool, acc: bool, acc_enabled: bool | None = None) -> None:
    """acc: the Haval ACC in control (cruiseState.driving). Older logs never set it: then cruiseState.enabled stands in."""
    self.seen_driving = self.seen_driving or acc
    if not self.seen_driving and acc_enabled is not None:
      acc = acc_enabled
    active = self.engaged and acc and not self.op_long and v > 0.5 and t - self.plan_t <= PLAN_MAX_AGE_S
    if not active:
      self.episode = None
      return
    self.totals["sh_on_s"] += dt
    if self.lead[0] and v > MIN_GAP_SPEED:
      self.totals["sh_gap_t"] += dt
      self.totals["sh_gap_s"] += self.lead[1] / v * dt
    ep = self.episode
    if ep is None:
      if a < START:
        self.episode = {"start": t, "peak": a, "peak_t": t, "op_at_peak": self.a_target, "brake": bool(brake)}
      return
    ep["brake"] = ep["brake"] or bool(brake)
    if a < ep["peak"]:
      ep.update(peak=a, peak_t=t, op_at_peak=self.a_target)
    if a > END:
      self.episode = None
      self._close(ep)

  def _close(self, ep: dict) -> None:
    if ep["peak"] > HARD or ep["brake"]:
      return
    self.totals["sh_hard"] += 1
    first = next((ht for ht, at in self.history if ep["start"] - WINDOW_S <= ht <= ep["peak_t"] and at <= START), None)
    earlier = first is not None and first <= ep["start"] - EARLY_S
    weaker = not earlier and ep["op_at_peak"] > ep["peak"] + WEAKER  # neither earlier nor as strong: openpilot would have been late
    if earlier:
      self.totals["sh_earlier"] += 1
    elif weaker:
      self.totals["sh_weaker"] += 1
    self.events.append({"t": round(ep["start"], 2), "peak": round(ep["peak"], 2), "op_first": None if first is None else round(first, 2),
                        "op_at_peak": round(ep["op_at_peak"], 2), "verdict": "antes" if earlier else ("tarde" if weaker else "junto")})

  def result(self) -> dict:
    out = {k: round(v, 3) for k, v in self.totals.items()}
    for k in ("sh_hard", "sh_earlier", "sh_weaker", "sh_fcw"):
      out[k] = int(self.totals[k])
    return out
