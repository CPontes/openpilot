from types import SimpleNamespace as NS

from openpilot.selfdrive.hexa import live


def device(started=True):
  return NS(maxTempC=61.4, thermalStatus="green", freeSpacePercent=57.6, memoryUsagePercent=29, cpuUsagePercent=[10, 20],
            networkType="wifi", started=started)


def parts():
  cs = NS(vEgo=10., steeringAngleDeg=-12.4, steeringPressed=False, brakePressed=False, gasPressed=False, leftBlinker=True,
          rightBlinker=False, leftBlindspot=False, rightBlindspot=True, cruiseState=NS(enabled=True, speed=16.7))
  sd = NS(enabled=True, alertText1="Take Control", alertText2="")
  return cs, sd, NS(latActive=True), NS(actuatorsOutput=NS(torqueOutputCan=-126)), NS(present=True, dRel=18.6, vLead=9.)


def gps(fix=True):
  return NS(hasFix=fix, horizontalAccuracy=3.26, latitude=-22.9, longitude=-43.1)


def test_driving():
  s = live.snapshot(*parts(), gps(), 1., device(), True)
  assert s["driving"] and s["v"] == 36 and s["wheel"] == -12 and s["torque"] == 50
  assert s["acc_set"] == 60 and s["lead"] == {"d": 19, "v": 32} and s["blinker"] == "esquerda"
  assert s["alert"] == "Take Control" and s["blindspot"] == {"left": False, "right": True}
  assert s["device"]["cpu"] == 15 and s["device"]["free"] == 58


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
