"""The car's signals the comma saw change in a drive: the "Aprovação na comma" trail's checks, done by the log (MISS-01).

comma's porting checklist asks for proof that the car port reads the seatbelt, doors, gear positions, pedals, turn signals
and blind spots. The owner does each one once, parked (Missões → Aprovação na comma); the drive's summary counts what
changed, and the phone ticks the mission by itself. Only counts and gear names, never a place or a time of day.
Car-agnostic: carState fields only, so it serves any car on the LizPilot for every car.
"""
GEARS = ("park", "reverse", "neutral", "drive")  # the four comma checks; any other position counts as "other"
GEAR_BITS = {g: 1 << i for i, g in enumerate(GEARS)}
GEAR_OTHER = 1 << len(GEARS)
STOPPED = 0.1  # m/s: the pedal checks are done parked

COUNT_KEYS = ("sg_belt", "sg_door", "sg_brake", "sg_gas", "sg_left", "sg_right", "sg_hazard", "sg_bsm_l", "sg_bsm_r", "sg_steer_fault")
MASK_KEYS = ("sg_gears",)
KEYS = COUNT_KEYS + MASK_KEYS


class SignalsSeen:
  def __init__(self):
    self.t = dict.fromkeys(KEYS, 0)
    self.prev: dict[str, bool] | None = None

  def on_car(self, cs) -> None:
    gear = str(getattr(cs, "gearShifter", "unknown"))
    self.t["sg_gears"] |= GEAR_BITS.get(gear, 0 if gear == "unknown" else GEAR_OTHER)
    left, right = bool(getattr(cs, "leftBlinker", False)), bool(getattr(cs, "rightBlinker", False))
    stopped = abs(getattr(cs, "vEgo", 0.)) < STOPPED
    now = {
      "belt": not bool(getattr(cs, "seatbeltUnlatched", False)),  # fastened again: one off and on
      "door": bool(getattr(cs, "doorOpen", False)),
      "brake": bool(getattr(cs, "brakePressed", False)) and stopped,
      "gas": bool(getattr(cs, "gasPressed", False)) and stopped,
      "left": left and not right,
      "right": right and not left,
      "hazard": left and right,
      "bsm_l": bool(getattr(cs, "leftBlindspot", False)),
      "bsm_r": bool(getattr(cs, "rightBlindspot", False)),
      # a steering fault lasts seconds: the qlog's carStates catch it. A button press lasts one frame and may not be there
      "steer_fault": bool(getattr(cs, "steerFaultTemporary", False) or getattr(cs, "steerFaultPermanent", False)),
    }
    if self.prev is not None:
      for k, on in now.items():
        # a rise counts once; the seatbelt counts when it is fastened again after being off
        if on and not self.prev[k]:
          self.t["sg_" + k] += 1
    self.prev = now

  def result(self) -> dict:
    return dict(self.t)


def merge(total: dict, part: dict) -> None:
  """A drive is made of minutes: counts add up, the gears seen join."""
  for k in COUNT_KEYS:
    total[k] = int(total.get(k, 0)) + int(part.get(k, 0))
  for k in MASK_KEYS:
    total[k] = int(total.get(k, 0)) | int(part.get(k, 0))


# *** What the comma learned of this car in a drive: the "Calibragem do veículo" trail (MISS-01, step C) ***
# extrinsicsCalibration, vehicleParameters and lateralTorqueParameters are all in the qlog (cereal/services.py). Only
# numbers: percentages, the learned steer ratio and the wheels against the GPS, never a place
CB_SUM_KEYS = ("cb_sr_km", "cb_v_n", "cb_v_wheel", "cb_v_gps")
CB_MAX_KEYS = ("cb_calibrated", "cb_cal_perc", "cb_tq_perc")
CB_LAST_KEYS = ("cb_sr", "cb_tq_laf")
CB_KEYS = CB_SUM_KEYS + CB_MAX_KEYS + CB_LAST_KEYS
WHEELS_GPS_MIN_SPEED = 8.  # m/s: slower, the GPS speed is too noisy to compare with
GPS_MAX_AGE = 1.  # s


class CalibrationSeen:
  def __init__(self):
    self.t: dict = dict.fromkeys(CB_SUM_KEYS + CB_MAX_KEYS, 0.)
    self.sr_valid = False
    self.gps: tuple[float, float] | None = None  # (t, speed m/s) of the last fix

  def on_calibration(self, ec) -> None:
    self.t["cb_calibrated"] = max(self.t["cb_calibrated"], 1. if str(ec.calStatus) == "calibrated" else 0.)
    self.t["cb_cal_perc"] = max(self.t["cb_cal_perc"], float(ec.calPerc))

  def on_parameters(self, vp) -> None:
    self.sr_valid = bool(vp.valid)
    if self.sr_valid:
      self.t["cb_sr"] = round(float(vp.steerRatio), 2)

  def on_torque(self, lt) -> None:
    self.t["cb_tq_perc"] = max(self.t["cb_tq_perc"], float(lt.calPerc))
    if lt.useParams:
      self.t["cb_tq_laf"] = round(float(lt.latAccelFactorFiltered), 2)

  def on_gps(self, t: float, g) -> None:
    if g.hasFix:
      self.gps = (t, float(g.speed))

  def on_car(self, t: float, v: float, dt: float) -> None:
    if self.sr_valid:
      self.t["cb_sr_km"] += v * dt / 1e3
    if self.gps is not None and t - self.gps[0] <= GPS_MAX_AGE and v > WHEELS_GPS_MIN_SPEED and self.gps[1] > WHEELS_GPS_MIN_SPEED:
      self.t["cb_v_n"] += 1
      self.t["cb_v_wheel"] += v
      self.t["cb_v_gps"] += self.gps[1]

  def result(self) -> dict:
    return dict(self.t)


def merge_calibration(total: dict, part: dict) -> None:
  """Sums add up, percentages and "calibrated" take the most, the learned values the latest minute that had them."""
  for k in CB_SUM_KEYS:
    total[k] = total.get(k, 0.) + part.get(k, 0.)
  for k in CB_MAX_KEYS:
    total[k] = max(total.get(k, 0.), part.get(k, 0.))
  for k in CB_LAST_KEYS:
    if k in part:
      total[k] = part[k]
