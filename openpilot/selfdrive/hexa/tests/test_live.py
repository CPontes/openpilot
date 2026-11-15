from types import SimpleNamespace as NS

from opendbc.car.structs import car
from openpilot.cereal import log
from openpilot.selfdrive.hexa import live


def device(started=True):
  return NS(maxTempC=61.4, thermalStatus="green", freeSpacePercent=57.6, memoryUsagePercent=29, cpuUsagePercent=[10, 20],
            networkType="wifi", started=started)


def parts():
  cs = NS(vEgo=10., steeringAngleDeg=-12.4, steeringPressed=False, brakePressed=False, gasPressed=False, leftBlinker=True,
          rightBlinker=False, leftBlindspot=False, rightBlindspot=True, cruiseState=NS(enabled=True, speed=-1, speedCluster=16.7, driving=True))
  sd = NS(enabled=True, alertText1="Take Control", alertText2="", alertStatus="userPrompt")
  return cs, sd, NS(latActive=True), NS(actuatorsOutput=NS(torqueOutputCan=-126)), NS(present=True, dRel=18.6, vLead=9., aLeadK=-0.43)


def gps(fix=True):
  return NS(hasFix=fix, horizontalAccuracy=3.26, latitude=-22.9, longitude=-43.1)


def test_driving():
  s = live.snapshot(*parts(), gps(), 1., device(), True)
  assert s["driving"] and s["v"] == 36 and s["wheel"] == -12 and s["torque"] == 50
  assert s["acc_set"] == 60 and s["lead"] == {"d": 19, "v": 32, "a": -0.4, "gap": 1.9} and s["blinker"] == "esquerda"  # 18.6 m at 36 km/h
  # a: the comma's filtered deceleration of the car ahead, for the copilot's "o carro da frente freou" (CTO review of app-11)
  assert s["alert"] == "Take Control" and s["alert_status"] == "userPrompt" and s["blindspot"] == {"left": False, "right": True}
  assert s["device"]["cpu"] == 15 and s["device"]["free"] == 58


def test_acc_paused_is_not_acc_active():
  cs, *rest = parts()
  cs.cruiseState.driving = False
  s = live.snapshot(cs, *rest, gps(), 1., device(), True)
  assert s["acc"] and not s["acc_active"]


def test_no_set_speed_shows_none():
  cs, *rest = parts()
  cs.cruiseState.speedCluster = 0.
  assert live.snapshot(cs, *rest, gps(), 1., device(), True)["acc_set"] is None


def test_acc_fields_exist_on_a_real_message():
  msg = car.CarState.new_message()
  msg.cruiseState.driving = True
  msg.cruiseState.speedCluster = 20.
  _, *rest = parts()
  s = live.snapshot(msg, *rest, gps(), 1., device(), True)
  assert s["acc_active"] and s["acc_set"] == 72


def test_position_never_goes_to_the_phone():
  s = live.snapshot(*parts(), gps(), 1., device(), True)
  assert s["gps"] == {"ok": True, "accuracy": 3.3}
  assert "-22.9" not in str(s) and "-43.1" not in str(s)


def test_old_gps_is_no_gps():
  assert live.snapshot(*parts(), gps(), 10., device(), True)["gps"]["ok"] is False
  assert live.snapshot(*parts(), gps(False), 1., device(), True)["gps"]["ok"] is False


def test_nothing_about_the_car_when_it_is_off_or_stale():
  for s in (live.snapshot(*parts(), gps(), 1., device(started=False), True), live.snapshot(*parts(), gps(), 1., device(), False)):
    assert s["driving"] is False and "v" not in s and s["device"]["temp"] == 61


# real messages from here on: a wrong field would raise only on the car


def event(name, **flags):
  return log.OnroadEvent.new_message(name=name, **flags).as_reader()


def message(name, **values):
  return getattr(log.Event.new_message(**{name: values}).as_reader(), name)


def test_every_text_is_about_a_real_event():
  assert set(live.EVENT_TEXT) <= set(log.OnroadEvent.EventName.schema.enumerants)


def test_why_not_puts_the_driver_first_in_portuguese():
  why = live.why_not([event("doorOpen", noEntry=True), event("pcmDisable", userDisable=True), event("userBookmark", warning=True)])
  assert why == ["o ACC desligou", "porta aberta"]


def test_an_event_with_no_text_shows_its_name():
  assert live.why_not([event("radarFault", noEntry=True)]) == ["radarFault"]


def test_why_not_repeats_nothing_and_stays_short():
  names = ["commIssue", "commIssueAvgFreq", "doorOpen", "parkBrake", "wrongGear", "overheat", "lowMemory"]
  why = live.why_not([event(n, noEntry=True) for n in names])
  assert len(why) == 4 and why.count("falha de comunicação") == 1


def test_lane_from_a_real_model_message():
  model = message("drivingModelData", laneLineMeta={"leftY": -1.61, "rightY": 1.72, "leftProb": .9, "rightProb": .2}, action={"desiredCurvature": .0123})
  assert live.lane(model) == {"left": -1.61, "right": 1.72, "left_ok": True, "right_ok": False, "curve": .0123}


def test_resume_checks_from_real_messages():
  dm = log.DriverMonitoringState.new_message(activePolicy="vision", alertLevel="none")
  dm.visionPolicyState.faceDetected = True
  cs = NS(standstill=True, gasPressed=False, brakePressed=False)
  c = live.resume_checks(cs, NS(enabled=True), NS(present=True), dm.as_reader())
  assert c == {"stopped": True, "engaged": True, "pedals": True, "looking": True, "lead": True}
  dm.visionPolicyState.isDistracted = True
  assert live.resume_checks(cs, NS(enabled=True), NS(present=False), dm.as_reader())["looking"] is False


def test_snapshot_carries_the_new_parts_only_when_given():
  plain = live.snapshot(*parts(), gps(), 1., device(), True)
  assert plain["lane"] is None and plain["resume"] is None and plain["why"] == []


def test_processes_down_from_a_real_message():
  manager = message("managerState", processes=[{"name": "loggerd", "running": True, "shouldBeRunning": True},
                                               {"name": "hexaweb", "running": False, "shouldBeRunning": True},
                                               {"name": "bridge", "running": False, "shouldBeRunning": False}])
  assert live.processes_down(manager) == ["hexaweb"]


def test_learned_from_real_messages():
  out = live.learned(message("lateralTorqueParameters", latAccelFactorFiltered=1.9, frictionCoefficientFiltered=.184, calPerc=100, useParams=True),
                     message("vehicleParameters", angleOffsetAverageDeg=-.9, steerRatio=17.78, stiffnessFactor=.987),
                     message("lateralDelay", lateralDelay=.5, calPerc=80))
  assert out == {"torque": {"factor": 1.9, "friction": .184, "percent": 100, "active": True}, "wheel": {"offset": -.9, "ratio": 17.8, "stiffness": .99},
                 "delay": {"s": .5, "percent": 80}}


def test_learned_shows_only_what_arrived():
  assert live.learned(None, None, None) == {}



def test_alert_status_from_a_real_message():
  sd = message("selfdriveState", enabled=True, alertText1="TAKE CONTROL IMMEDIATELY", alertStatus="critical")
  cs, _, cc, out, lead = parts()
  assert live.snapshot(cs, sd, cc, out, lead, gps(), 1., device(), True)["alert_status"] == "critical"


def test_recent_errors_from_swaglog_files(tmp_path):
  import json
  lines = [
    {"msg$s": "all good", "levelnum$i": 20, "level$s": "INFO", "created$f": 100.},
    {"msg$s": "modeld lagging", "levelnum$i": 40, "level$s": "ERROR", "created$f": 200.4, "ctx": {"daemon$s": "modeld", "dongle_id$s": "secret"}},
    {"msg$s": "hexaweb: server failed", "levelnum$i": 40, "level$s": "ERROR", "created$f": 300., "name$s": "swaglog",
     "exc_info$s": "Traceback (most recent call last):\n  File x\nOSError: [Errno 98] Address in use", "ctx": {"daemon$s": "hexaweb"}},
  ]
  (tmp_path / "swaglog.0000000001").write_text("\n".join(json.dumps(x) for x in lines[:2]) + "\nnot json ERROR\n")
  (tmp_path / "swaglog.0000000002").write_text(json.dumps(lines[2]) + "\n")
  (tmp_path / "other.log").write_text(json.dumps(lines[1]))
  errors = live.recent_errors(tmp_path)
  assert errors == [{"t": 300, "process": "hexaweb", "msg": "hexaweb: server failed", "error": "OSError: [Errno 98] Address in use"},
                    {"t": 200, "process": "modeld", "msg": "modeld lagging", "error": ""}]
  assert "secret" not in str(errors)
  assert live.recent_errors(tmp_path / "missing") == []


def test_no_time_gap_when_almost_stopped():
  cs, sd, cc, out, lead = parts()
  cs.vEgo = 2.
  assert live.snapshot(cs, sd, cc, out, lead, gps(), 1., device(), True)["lead"]["gap"] is None


def test_the_car_right_now():
  cs, sd, cc, out, lead = parts()
  cc.orientationNED = [0., 0.05, 0.]
  car = live.snapshot(cs, sd, cc, out, lead, gps(), 1., device(), True, peripheral=NS(voltage=12480))["car"]
  assert car["grade"] == 5.0 and car["v12"] == 12.5 and car["roll"] == 0.0
