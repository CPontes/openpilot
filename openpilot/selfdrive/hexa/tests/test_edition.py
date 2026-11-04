"""The generic edition (CPontes/lizpilot): in any car but a GWM, what was only tested on the Haval is off and out of sight.

The rest of these tests run as the Haval build (conftest.py). This file checks the generic edition in a fresh process,
since the edition is read once, when the modules load.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]  # the repository root, where launch_env.sh is

GENERIC_CHECKS = textwrap.dedent('''
  from opendbc.car.structs import car
  from openpilot.selfdrive.hexa import comma_ctl, edition, missions, updates
  from openpilot.selfdrive.hexa.options import OPTIONS, get_option, supported
  from openpilot.selfdrive.controls.lib.desire_helper import auto_lane_change_delay

  assert edition.EDITION == "generic"

  class Store(dict):
    def get(self, k):
      return dict.get(self, k)
    def get_bool(self, k):
      return bool(dict.get(self, k, False))

  def store(brand=None, **values):
    s = Store(values)
    if brand is not None:
      s["CarParamsPersistent"] = car.CarParams.new_message(brand=brand).to_bytes()
    return s

  gwm_only = {o.key for o in OPTIONS if o.brands}
  assert {"LaneOffsetEnabled", "AutoLaneChange", "AlwaysOnLateral", "LowSpeedFilter", "StopCloser", "AccLostAlert"} <= gwm_only
  for st in (store(), store("toyota"), store("hyundai")):  # unknown car (before the first drive) or another brand
    shown = {o.key for o in OPTIONS if supported(o, st)}
    assert not shown & gwm_only, shown & gwm_only
    assert {"RadarAlert", "LeadDepartAlert", "IsLdwEnabled", "HexaWebEnabled"} <= shown, shown
  shown = {o.key for o in OPTIONS if supported(o, store("gwm"))}
  assert gwm_only <= shown  # a GWM gets them, as in the Haval build
  assert edition.car_brand(Store(CarParamsPersistent=b"not capnp")) is None  # never raises

  # lane change by the turn signal: the release torque is in the Haval's units
  assert auto_lane_change_delay(store("toyota", AutoLaneChange=True, AutoLaneChangeDelay=1)) is None
  assert auto_lane_change_delay(store(None, AutoLaneChange=True, AutoLaneChangeDelay=1)) is None
  assert auto_lane_change_delay(store("gwm", AutoLaneChange=True, AutoLaneChangeDelay=1)) == 2.

  # missions: only what any car can test, from zero, without the owner's drives
  assert missions.CATALOG and {m.trail for m in missions.CATALOG} <= set(missions.GENERIC_TRAILS)
  assert all(m.done == 0 and not m.proof and not m.route for m in missions.CATALOG)
  pub = missions.public()
  assert all(not m["tries"] for m in pub["missions"]) and {t["key"] for t in pub["trails"]} <= set(missions.GENERIC_TRAILS)

  assert updates.OFFERED_BRANCHES == ("lizpilot",)
  alpha = comma_ctl.get_item("AlphaLongitudinalEnabled")
  assert alpha.warnings and not any("Haval" in w for w in alpha.warnings)
  assert not any("Haval" in title for _, title in comma_ctl.LONG_STEPS)
  print("generic ok")
''')


def test_generic_edition_hides_what_only_the_haval_tested():
  env = dict(os.environ, LIZPILOT_EDITION="generic")
  out = subprocess.run([sys.executable, "-c", GENERIC_CHECKS], env=env, capture_output=True, text=True, timeout=120)
  assert out.returncode == 0 and "generic ok" in out.stdout, out.stdout + out.stderr


def test_the_haval_build_is_unchanged_by_the_brand_rule():
  # conftest.py: this process is the Haval build, where an unknown car is still the Haval
  from openpilot.selfdrive.hexa import edition
  from openpilot.selfdrive.hexa.options import OPTIONS, supported
  assert edition.EDITION == "h6gt"
  assert all(supported(o, {}) for o in OPTIONS if not o.blocked)


def test_launch_env_forces_no_car(tmp_path):
  text = (REPO / "launch_env.sh").read_text()
  assert "export FINGERPRINT=GWM_HAVAL_H6" not in text
  # the opt-in file: only capitals, digits and "_" reach FINGERPRINT, and nothing in it runs
  fp = tmp_path / "fingerprint"
  script = text.replace("/data/lizpilot/fingerprint", str(fp))
  (tmp_path / "env.sh").write_text(script)
  for content, expected in (("GWM_HAVAL_H6\n", "GWM_HAVAL_H6"), ("X; touch PWNED $(id)", "XPWNED"), (None, "")):
    if content is None:
      fp.unlink(missing_ok=True)
    else:
      fp.write_text(content)
    out = subprocess.run(["bash", "-c", f'unset FINGERPRINT; source "{tmp_path / "env.sh"}" >/dev/null 2>&1; printf "%s" "$FINGERPRINT"'],
                         cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert out.stdout == expected, (content, out.stdout)
    assert not (tmp_path / "PWNED").exists()
