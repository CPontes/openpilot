"""What the phone shows live. Read only, built when the panel asks: nothing here runs in the background.

Positions never leave this function: the GPS shows up only as working or not.
"""
from openpilot.selfdrive.selfdrived.traffic_alerts import driver_attentive

STEER_MAX = 253
GPS_MAX_AGE_S = 3.

# why openpilot doesn't engage or dropped out. An event not listed shows its own name
EVENT_TEXT = {
  "pcmDisable": "o ACC desligou",
  "buttonCancel": "cancelar na alavanca",
  "wrongCarMode": "ACC não está pronto: puxe a alavanca",
  "pedalPressed": "pedal pisado",
  "brakeHold": "freio segurando o carro",
  "parkBrake": "freio de mão",
  "wrongGear": "câmbio fora de D",
  "reverseGear": "marcha à ré",
  "doorOpen": "porta aberta",
  "seatbeltNotLatched": "cinto solto",
  "espDisabled": "controle de estabilidade desligado",
  "speedTooHigh": "velocidade alta demais",
  "cruiseMismatch": "ACC e openpilot discordam",
  "controlsMismatch": "o panda recusou o comando",
  "steerUnavailable": "direção indisponível",
  "steerTempUnavailable": "direção indisponível por um momento",
  "calibrationIncomplete": "ainda calibrando",
  "calibrationInvalid": "calibração inválida",
  "calibrationRecalibrating": "recalibrando",
  "commIssue": "falha de comunicação",
  "commIssueAvgFreq": "falha de comunicação",
  "processNotRunning": "um processo parou",
  "tooDistracted": "bloqueado por distração",
  "driverDistracted3": "motorista distraído",
  "driverUnresponsive3": "motorista sem resposta",
  "overheat": "comma quente",
  "outOfSpace": "disco cheio",
  "lowMemory": "memória cheia",
  "posenetInvalid": "visão instável",
  "sensorDataInvalid": "falha nos sensores",
  "canError": "falha na rede do carro",
  "relayMalfunction": "falha no chicote",
  "modeldLagging": "modelo atrasado",
  "selfdrivedLagging": "sistema atrasado",
}


def why_not(events) -> list[str]:
  """What stops openpilot right now, the driver's own actions first. Never more than the phone can show."""
  mine = [e for e in events if e.userDisable]
  others = [e for e in events if not e.userDisable and (e.noEntry or e.softDisable or e.immediateDisable)]
  out: list[str] = []
  for e in mine + others:
    text = EVENT_TEXT.get(str(e.name), str(e.name))
    if text not in out:
      out.append(text)
  return out[:4]


def lane(model) -> dict:
  m = model.laneLineMeta
  return {"left": round(float(m.leftY), 2), "right": round(float(m.rightY), 2), "left_ok": bool(m.leftProb > .5), "right_ok": bool(m.rightProb > .5),
          "curve": round(float(model.action.desiredCurvature), 4)}


def resume_checks(cs, sd, lead, monitoring) -> dict:
  """The same conditions selfdrived checks to pull away from a stop, to see why it didn't."""
  return {"stopped": bool(cs.standstill), "engaged": bool(sd.enabled), "pedals": not (cs.gasPressed or cs.brakePressed),
          "looking": driver_attentive(monitoring), "lead": bool(lead.present)}


def device_state(ds) -> dict:
  return {
    "temp": round(float(ds.maxTempC)),
    "thermal": str(ds.thermalStatus),
    "free": round(float(ds.freeSpacePercent)),
    "memory": int(ds.memoryUsagePercent),
    "cpu": round(sum(ds.cpuUsagePercent) / len(ds.cpuUsagePercent)) if len(ds.cpuUsagePercent) else 0,
    "network": str(ds.networkType),
  }


def processes_down(manager) -> list[str]:
  return sorted(p.name for p in manager.processes if p.shouldBeRunning and not p.running)


def learned(torque, vehicle, delay) -> dict:
  """What the car learned. Each part only shows up after its message arrived in this run."""
  out: dict = {}
  if torque is not None:
    out["torque"] = {"factor": round(float(torque.latAccelFactorFiltered), 2), "friction": round(float(torque.frictionCoefficientFiltered), 3),
                     "percent": int(torque.calPerc), "active": bool(torque.useParams)}
  if vehicle is not None:
    out["wheel"] = {"offset": round(float(vehicle.angleOffsetAverageDeg), 1), "ratio": round(float(vehicle.steerRatio), 1),
                    "stiffness": round(float(vehicle.stiffnessFactor), 2)}
  if delay is not None:
    out["delay"] = {"s": round(float(delay.lateralDelay), 2), "percent": int(delay.calPerc)}
  return out


def snapshot(cs, sd, cc, out, lead, gps, gps_age: float, ds, car_fresh: bool, events=(), model=None, monitoring=None) -> dict:
  gps_ok = bool(gps.hasFix) and gps_age < GPS_MAX_AGE_S
  live = {"device": device_state(ds), "gps": {"ok": gps_ok, "accuracy": round(float(gps.horizontalAccuracy), 1) if gps_ok else None}}
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
    "why": why_not(events),
    "lane": lane(model) if model is not None else None,
    "resume": resume_checks(cs, sd, lead, monitoring) if monitoring is not None else None,
  }
