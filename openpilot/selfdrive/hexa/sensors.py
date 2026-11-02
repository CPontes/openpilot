"""What the car went through, from sensors every openpilot car has: the comma's own IMU and GPS, the calibrated pose,
the panda's 12 V reading and carState. Owner's request: "medir tudo que está acontecendo com o carro", the Haval first,
any car after. Nothing here reads a brand's own CAN signals, so it works the same on every supported car.

- grade: road grade in %, from the calibrated pitch (carControl.orientationNED, controlsd.py:174-175). Uphill > 0, the
  sign Toyota and Ford use for gravity (opendbc toyota/carcontroller.py:204, ford/carcontroller.py:138).
- roll: how much the car leans sideways, in degrees, same source.
- rough / bumps: the road under the comma. Vertical acceleration from the IMU (accelerometer, 104 Hz) minus its own slow
  average; rough is its RMS per moving second, bumps are jolts above BUMP m/s², at most one per BUMP_GAP_S.
- 12 V: the car battery as the panda sees it (peripheralState.voltage).
- wheels: spread between the four wheel speeds on straight, steady driving. A tire with less air turns faster.
  [hipótese] a spread that stays above ~1 % hints at a low tire; it is a hint, never a tire pressure.
"""
import math

HP_TAU_S = 0.5  # the slow average taken out of the vertical acceleration
BUMP = 3.0  # m/s², a jolt
BUMP_GAP_S = 1.0
GRADE_TAU_S = 1.0
MIN_SPEED = 2.0  # m/s
WHEEL_MIN_SPEED = 40 / 3.6
WHEEL_MAX_STEER_DEG = 3.
WHEEL_MAX_ACCEL = 0.3  # m/s²
V12_MIN_OK = 11.8  # V, below this with the engine off the battery is low [hipótese] for a PHEV's 12 V

SUM_KEYS = ("se_rough_ss", "se_rough_t", "se_bumps", "se_wheel_s", "se_wheel_t")
MAX_KEYS = ("se_grade_up", "se_grade_down", "se_roll", "se_decel", "se_lat")
MIN_KEYS = ("se_v12_min",)
KEYS = SUM_KEYS + MAX_KEYS + MIN_KEYS


def grade_pct(pitch_rad: float) -> float:
  return math.tan(pitch_rad) * 100.


class CarSensors:
  def __init__(self):
    self.t: dict[str, float] = dict.fromkeys(SUM_KEYS + MAX_KEYS, 0.0)
    self.t["se_v12_min"] = 0.  # 0 = no reading
    self.moving = False
    self.avg: list[float] | None = None  # slow average of each IMU axis: the one with gravity is the vertical
    self.acc_t: float | None = None
    self.last_bump = -1e9
    self.grade: float | None = None
    self.pitch_t: float | None = None

  def on_car(self, v: float, a: float, steer_deg: float, wheels: tuple[float, float, float, float] | None, dt: float) -> None:
    self.moving = v > MIN_SPEED
    if not self.moving:
      return
    self.t["se_decel"] = max(self.t["se_decel"], -a)
    if wheels and v > WHEEL_MIN_SPEED and abs(steer_deg) < WHEEL_MAX_STEER_DEG and abs(a) < WHEEL_MAX_ACCEL:
      mean = sum(wheels) / 4.
      if mean > 0:
        self.t["se_wheel_s"] += (max(wheels) - min(wheels)) / mean * 100. * dt
        self.t["se_wheel_t"] += dt

  def on_pose(self, t: float, roll: float, pitch: float) -> None:
    """Calibrated orientation (car frame), radians."""
    g = grade_pct(pitch)
    if self.grade is None or self.pitch_t is None:
      self.grade = g
    else:
      k = min(1., max(0., t - self.pitch_t) / GRADE_TAU_S)
      self.grade += k * (g - self.grade)
    self.pitch_t = t
    if self.moving:
      self.t["se_grade_up"] = max(self.t["se_grade_up"], self.grade)
      self.t["se_grade_down"] = max(self.t["se_grade_down"], -self.grade)
      self.t["se_roll"] = max(self.t["se_roll"], abs(math.degrees(roll)))

  def on_accel(self, t: float, xyz: tuple[float, float, float]) -> None:
    """Raw IMU, gravity included. Whatever way the comma is mounted, the vertical axis is the one gravity sits on."""
    if self.acc_t is None or self.avg is None:
      self.acc_t, self.avg = t, list(xyz)
      return
    dt = max(0., min(t - self.acc_t, 0.1))
    self.acc_t = t
    k = min(1., dt / HP_TAU_S)
    for i in range(3):
      self.avg[i] += k * (xyz[i] - self.avg[i])
    if not self.moving:
      return
    up = max(range(3), key=lambda i: abs(self.avg[i]))
    jolt = xyz[up] - self.avg[up]
    self.t["se_rough_ss"] += jolt * jolt * dt
    self.t["se_rough_t"] += dt
    if abs(jolt) > BUMP and t - self.last_bump > BUMP_GAP_S:
      self.t["se_bumps"] += 1
      self.last_bump = t

  def on_motion(self, lateral: float) -> None:
    """deviceMotion, device frame without gravity: y is sideways."""
    if self.moving:
      self.t["se_lat"] = max(self.t["se_lat"], abs(lateral))

  def on_voltage(self, volts: float) -> None:
    if volts > 5.:  # 0 = no panda reading
      self.t["se_v12_min"] = volts if self.t["se_v12_min"] == 0. else min(self.t["se_v12_min"], volts)

  def result(self) -> dict:
    out = {k: round(v, 3) for k, v in self.t.items()}
    out["se_bumps"] = int(self.t["se_bumps"])
    return out


def merge(total: dict, part: dict) -> None:
  """A drive is made of minutes: sums add up, maxima take the larger, the 12 V minimum the lower nonzero."""
  for k in SUM_KEYS:
    total[k] = total.get(k, 0.) + part.get(k, 0.)
  for k in MAX_KEYS:
    total[k] = max(total.get(k, 0.), part.get(k, 0.))
  for k in MIN_KEYS:
    a, b = total.get(k, 0.), part.get(k, 0.)
    total[k] = b if not a else (a if not b else min(a, b))


def live(cc, peripheral, gps, cs) -> dict:
  """The car right now, for the phone: grade, lean, 12 V, altitude and wheel spread."""
  o = list(getattr(cc, "orientationNED", []) or [])
  ws = getattr(cs, "wheelSpeeds", None)
  wheels = (ws.fl, ws.fr, ws.rl, ws.rr) if ws is not None else None
  spread = None
  if wheels and sum(wheels) > 4 * WHEEL_MIN_SPEED:
    spread = round((max(wheels) - min(wheels)) / (sum(wheels) / 4.) * 100., 1)
  volts = getattr(peripheral, "voltage", 0) / 1000. if peripheral is not None else 0.
  return {"grade": round(grade_pct(o[1]), 1) if len(o) == 3 else None, "roll": round(math.degrees(o[0]), 1) if len(o) == 3 else None,
          "v12": round(volts, 1) if volts > 5. else None,
          "alt": round(gps.altitude) if gps is not None and getattr(gps, "hasFix", False) and hasattr(gps, "altitude") else None,
          "wheels": spread}
