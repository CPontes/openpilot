"""What the car went through, from sensors every openpilot car has: the comma's own IMU and GPS, the calibrated pose,
the panda's 12 V reading and carState. Owner's request: "medir tudo que está acontecendo com o carro", the Haval first,
any car after. Nothing here reads a brand's own CAN signals, so it works the same on every supported car.

- grade: road grade in %, from the calibrated pitch (carControl.orientationNED, controlsd.py:174-175). Uphill > 0, the
  sign Toyota and Ford use for gravity (opendbc toyota/carcontroller.py:204, ford/carcontroller.py:138).
- roll: how much the car leans sideways, in degrees, same source.
- lat: the force in a curve, speed times yaw rate (deviceMotion.angularVelocityDevice.z). accelerationDevice has the
  centripetal part filtered out: it read 2.8 m/s² where the raw IMU showed 4.8.
- 12 V: the car battery as the panda sees it (peripheralState.voltage).
- wheels: spread between the four wheel speeds on straight, steady driving. A tire with less air turns faster.
  [hipótese] a spread that stays above ~1 % hints at a low tire; it is a hint, never a tire pressure.
"""
import math

GRADE_TAU_S = 1.0
MIN_SPEED = 2.0  # m/s
WHEEL_MIN_SPEED = 40 / 3.6
WHEEL_MAX_STEER_DEG = 3.
WHEEL_MAX_ACCEL = 0.3  # m/s²
V12_MIN_OK = 11.8  # V, below this with the engine off the battery is low [hipótese] for a PHEV's 12 V
# Automatic mark (owner's decision, 02/11): a very hard brake becomes a mark with its video. A hit can't: the summary reads
# the qlog, where the IMU comes at 1 Hz (cereal/services.py), and a hit is over in a tenth of a second.
HARD_BRAKE = -6.  # m/s², the car's own deceleration; never held for HARD_BRAKE_S in 130 min of the owner's drives
HARD_BRAKE_S = .3
HARD_BRAKE_MIN_SPEED = 3.  # m/s
AUTO_GAP_S = 10.  # one mark per event, not one per sample

SUM_KEYS = ("se_wheel_s", "se_wheel_t", "se_hard_brakes")
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
    self.v = 0.
    self.grade: float | None = None
    self.pitch_t: float | None = None
    self.events: list[tuple[float, str]] = []  # (t, "hardbrake"): automatic marks
    self.last_event = -1e9
    self.brake_since: float | None = None
    self.t_car = 0.

  def _event(self, t: float, kind: str) -> None:
    if t - self.last_event > AUTO_GAP_S:
      self.events.append((round(t, 2), kind))
      self.t["se_hard_brakes"] += 1
    self.last_event = t

  def on_car(self, v: float, a: float, steer_deg: float, wheels: tuple[float, float, float, float] | None, dt: float,
             t: float | None = None) -> None:
    self.t_car = self.t_car + dt if t is None else t
    hard = v > HARD_BRAKE_MIN_SPEED and a <= HARD_BRAKE
    self.brake_since = (self.brake_since if self.brake_since is not None else self.t_car) if hard else None
    if self.brake_since is not None and self.t_car - self.brake_since >= HARD_BRAKE_S:
      self._event(self.t_car, "hardbrake")
    self.moving = v > MIN_SPEED
    self.v = v
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

  def on_motion(self, yaw_rate: float) -> None:
    """deviceMotion.angularVelocityDevice.z, rad/s."""
    if self.moving:
      self.t["se_lat"] = max(self.t["se_lat"], abs(self.v * yaw_rate))

  def on_voltage(self, volts: float) -> None:
    if volts > 5.:  # 0 = no panda reading
      self.t["se_v12_min"] = volts if self.t["se_v12_min"] == 0. else min(self.t["se_v12_min"], volts)

  def result(self) -> dict:
    return {k: round(v, 3) for k, v in self.t.items()}


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
  """The car right now, for the phone: grade, lean, 12 V and wheel spread."""
  o = list(getattr(cc, "orientationNED", []) or [])
  ws = getattr(cs, "wheelSpeeds", None)
  wheels = (ws.fl, ws.fr, ws.rl, ws.rr) if ws is not None else None
  spread = None
  if wheels and sum(wheels) > 4 * WHEEL_MIN_SPEED:
    spread = round((max(wheels) - min(wheels)) / (sum(wheels) / 4.) * 100., 1)
  volts = getattr(peripheral, "voltage", 0) / 1000. if peripheral is not None else 0.
  return {"grade": round(grade_pct(o[1]), 1) if len(o) == 3 else None, "roll": round(math.degrees(o[0]), 1) if len(o) == 3 else None,
          "v12": round(volts, 1) if volts > 5. else None,
          "wheels": spread}
