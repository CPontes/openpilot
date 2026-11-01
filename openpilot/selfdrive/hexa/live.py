"""What the phone shows live. Read only, built when the panel asks: nothing here runs in the background.

Positions never leave this function: the GPS shows up only as working or not.
"""
STEER_MAX = 253
GPS_MAX_AGE_S = 3.


def snapshot(cs, sd, cc, out, lead, gps, gps_age: float, ds, car_fresh: bool) -> dict:
  device = {
    "temp": round(float(ds.maxTempC)),
    "thermal": str(ds.thermalStatus),
    "free": round(float(ds.freeSpacePercent)),
    "memory": int(ds.memoryUsagePercent),
    "cpu": round(sum(ds.cpuUsagePercent) / len(ds.cpuUsagePercent)) if len(ds.cpuUsagePercent) else 0,
    "network": str(ds.networkType),
  }
  gps_ok = bool(gps.hasFix) and gps_age < GPS_MAX_AGE_S
  live = {"device": device, "gps": {"ok": gps_ok, "accuracy": round(float(gps.horizontalAccuracy), 1) if gps_ok else None}}
  if not ds.started or not car_fresh:
    return {**live, "driving": False}
  return {
    **live,
    "driving": True,
    "v": round(cs.vEgo * 3.6),
    "wheel": round(float(cs.steeringAngleDeg)),
    "engaged": bool(sd.enabled),
    "steering": bool(cc.latActive),
    "driver": bool(cs.steeringPressed),
    "brake": bool(cs.brakePressed),
    "gas": bool(cs.gasPressed),
    "acc": bool(cs.cruiseState.enabled),
    "acc_set": round(cs.cruiseState.speed * 3.6) if cs.cruiseState.speed > 0 else None,
    "torque": round(100 * abs(out.actuatorsOutput.torqueOutputCan) / STEER_MAX),
    "lead": {"d": round(float(lead.dRel)), "v": round(lead.vLead * 3.6)} if lead.present else None,
    "blinker": "esquerda" if cs.leftBlinker else ("direita" if cs.rightBlinker else ""),
    "blindspot": {"left": bool(cs.leftBlindspot), "right": bool(cs.rightBlindspot)},
    "alert": " ".join(t for t in (sd.alertText1, sd.alertText2) if t),
  }
