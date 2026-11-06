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
  assert auto_lane_change_delay(store("toyota", AutoLaneChange=True, AutoLaneChangeDelay=1), "toyota") is None
  assert auto_lane_change_delay(store("gwm", AutoLaneChange=True, AutoLaneChangeDelay=1), "toyota") is None  # last car a Haval
  assert auto_lane_change_delay(store(None, AutoLaneChange=True, AutoLaneChangeDelay=1), "gwm") == 2.

  # missions: only what any car can test, from zero, without the owner's drives
  ap = {k for k, _ in missions.TRAILS if k.startswith("ap-")}
  assert missions.CATALOG and {m.trail for m in missions.CATALOG} <= set(missions.GENERIC_TRAILS) | ap
  assert all(m.done == 0 and not m.proof and not m.route for m in missions.CATALOG)
  pub = missions.public()
  assert all(not m["tries"] for m in pub["missions"]) and {t["key"] for t in pub["trails"]} <= set(missions.GENERIC_TRAILS) | ap
  # the approval of a new car (MISS-01 B) comes first, all of it; the Haval's own "coleta" isn't there
  assert [t["key"] for t in pub["trails"]][:5] == ["ap-carro", "ap-sinais", "ap-engate", "ap-direcao", "ap-envio"]
  assert {m.key for m in missions.APPROVAL} <= {m["key"] for m in pub["missions"]} and "coleta" not in {t["key"] for t in pub["trails"]}

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


def launch_env_fingerprint(tmp_path, edition, fp_content):
  """FINGERPRINT after sourcing a copy of launch_env.sh next to an edition.txt, as in the repository."""
  text = (REPO / "launch_env.sh").read_text()
  fp = tmp_path / "fingerprint"
  (tmp_path / "launch_env.sh").write_text(text.replace("/data/lizpilot/fingerprint", str(fp)))
  hexa = tmp_path / "openpilot" / "selfdrive" / "hexa"
  hexa.mkdir(parents=True, exist_ok=True)
  if edition is None:
    (hexa / "edition.txt").unlink(missing_ok=True)
  else:
    (hexa / "edition.txt").write_text(edition)
  if fp_content is None:
    fp.unlink(missing_ok=True)
  else:
    fp.write_text(fp_content)
  out = subprocess.run(["bash", "-c", f'unset FINGERPRINT; source "{tmp_path / "launch_env.sh"}" >/dev/null 2>&1; printf "%s" "$FINGERPRINT"'],
                       cwd=tmp_path, capture_output=True, text=True, timeout=30)
  return out.stdout


def launch_env_fingerprint_raw(tmp_path, raw: bytes):
  """FINGERPRINT from launch_env.sh with edition.txt holding these exact bytes and no opt-in file."""
  launch_env_fingerprint(tmp_path, "", None)
  (tmp_path / "openpilot" / "selfdrive" / "hexa" / "edition.txt").write_bytes(raw)
  out = subprocess.run(["bash", "-c", f'unset FINGERPRINT; source "{tmp_path / "launch_env.sh"}" >/dev/null 2>&1; printf "%s" "$FINGERPRINT"'],
                       cwd=tmp_path, capture_output=True, text=True, timeout=30)
  return out.stdout


def test_launch_env_the_haval_build_is_always_the_haval(tmp_path):
  for edition in ("h6gt\n", None, "something else"):  # missing or unknown: the canal, as it always was
    for fp in (None, "TOYOTA_RAV4\n"):  # the opt-in file doesn't change the Haval build
      assert launch_env_fingerprint(tmp_path, edition, fp) == "GWM_HAVAL_H6", (edition, fp)


def test_launch_env_generic_recognizes_the_car(tmp_path):
  # the opt-in file: only capitals, digits and "_" reach FINGERPRINT, and nothing in it runs
  for content, expected in (("GWM_HAVAL_H6\n", "GWM_HAVAL_H6"), ("X; touch PWNED $(id)", "XPWNED"), (None, "")):
    assert launch_env_fingerprint(tmp_path, "generic\n", content) == expected, content
    assert not (tmp_path / "PWNED").exists()


def test_the_edition_file(tmp_path):
  from openpilot.selfdrive.hexa import edition
  f = tmp_path / "edition.txt"
  for content, expected in (("generic\n", "generic"), ("h6gt", "h6gt"), ("GENERIC", "h6gt"), ("", "h6gt")):
    f.write_text(content)
    assert edition.read_edition(f) == expected, content
  assert edition.read_edition(tmp_path / "missing.txt") == "h6gt"


def test_python_and_the_shell_read_the_edition_alike(tmp_path):
  # a word they disagreed on could leave the shell generic (no forced Haval) and Python in the Haval build
  from openpilot.selfdrive.hexa import edition
  f = tmp_path / "edition.txt"
  cases = (b"generic\n", b"\xef\xbb\xbfgeneric\n", b"gen eric", "generic".encode("utf-16"), b"GENERIC", b"h6gt\r\n",
           b"\xff\xfe\x00", b"generic" + b"x" * 20, b"   generic   ")
  for raw in cases:
    f.write_bytes(raw)
    # the very lines of launch_env.sh, sourced against this file
    shell = launch_env_fingerprint_raw(tmp_path, raw)
    py = edition.read_edition(f)  # never raises
    assert (py == "generic") == (shell != "GWM_HAVAL_H6"), (raw, py, shell)

