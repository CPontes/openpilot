#!/usr/bin/env python3
"""
Fit the GWM Haval H6 gas/brake command law from drives on the stock ACC.

ACC_CMD (0x143) carries a gas and a brake command, not an acceleration, so the
port has to map openpilot's desired acceleration to those pedals. Instead of
guessing, this regresses what the stock camera commanded against the
acceleration the car reached LAG seconds later:

  GAS_CMD   = a*accel + b*v + c*sin(pitch) + d     (BRAKE_OR_GAS_REQ == GAS_REQ)
  BRAKE_CMD = a*accel + b*v + c*sin(pitch) + d     (BRAKE_OR_GAS_REQ == BRAKE_REQ)

Commands are in DBC units, the same ones gwmcan.create_longitudinal_command writes.
It also sweeps the accel threshold that best separates gas from brake.

Use drives with the stock ACC engaged and openpilot longitudinal OFF:
  fit_longitudinal.py <rlog files or directory> [--lag 0.3]
"""
import argparse
import glob
import os
from dataclasses import dataclass

import numpy as np

DBC_NAME = "gwm_haval_h6_mk3_generated"
CAMERA_BUS = 2
GAS_REQ, BRAKE_REQ = 12, 13


@dataclass
class Fit:
  coef: np.ndarray  # accel, v, sin(pitch), intercept
  r2: float
  n: int

  def __str__(self):
    a, b, c, d = self.coef
    return f"{a:9.2f}*accel {b:+8.3f}*v {c:+9.2f}*sin(pitch) {d:+9.2f}   (R2 {self.r2:.2f}, n={self.n})"


def fit_linear(accel, v, sin_pitch, cmd) -> Fit:
  X = np.column_stack([accel, v, sin_pitch, np.ones_like(accel)])
  coef, *_ = np.linalg.lstsq(X, cmd, rcond=None)
  pred = X @ coef
  ss_res = float(np.sum((cmd - pred) ** 2))
  ss_tot = float(np.sum((cmd - np.mean(cmd)) ** 2)) or 1.0
  return Fit(coef, 1.0 - ss_res / ss_tot, len(cmd))


def best_threshold(accel, is_gas):
  """Accel threshold that best predicts gas vs brake, and its agreement."""
  best = (0.0, 0.0)
  for th in np.arange(-2.0, 1.0, 0.02):
    agree = float(np.mean((accel > th) == is_gas))
    if agree > best[1]:
      best = (float(th), agree)
  return best


def build_samples(t_cmd, req, gas, brake, t_cs, a_ego, v_ego, t_pose, pitch, lag):
  """Pair each camera command with the car state LAG seconds later."""
  t_cmd, t_cs = np.asarray(t_cmd), np.asarray(t_cs)
  keep = (t_cmd + lag >= t_cs[0]) & (t_cmd + lag <= t_cs[-1])
  t = t_cmd[keep]
  accel = np.interp(t + lag, t_cs, a_ego)
  v = np.interp(t, t_cs, v_ego)
  sin_pitch = np.sin(np.interp(t, t_pose, pitch)) if len(t_pose) else np.zeros_like(t)
  return np.asarray(req)[keep], np.asarray(gas)[keep], np.asarray(brake)[keep], accel, v, sin_pitch


def read_logs(paths):
  from opendbc.can.parser import CANParser
  from openpilot.tools.lib.logreader import LogReader

  cp = CANParser(DBC_NAME, [("ACC_CMD", 50)], CAMERA_BUS)
  t_cmd, req, gas, brake = [], [], [], []
  t_cs, a_ego, v_ego = [], [], []
  t_pose, pitch = [], []
  op_long = False
  for path in paths:
    for msg in LogReader(path):
      which = msg.which()
      t = msg.logMonoTime * 1e-9
      if which == "can":
        frames = [(c.address, c.dat, c.src) for c in msg.can if c.src == CAMERA_BUS]
        if frames and cp.update([(msg.logMonoTime, frames)]):
          vl = cp.vl["ACC_CMD"]
          t_cmd.append(t)
          req.append(int(vl["BRAKE_OR_GAS_REQ"]))
          gas.append(vl["GAS_CMD"])
          brake.append(vl["BRAKE_CMD"])
      elif which == "carState":
        t_cs.append(t)
        a_ego.append(msg.carState.aEgo)
        v_ego.append(msg.carState.vEgo)
      elif which in ("deviceMotion", "livePose"):  # livePose in older openpilot versions
        t_pose.append(t)
        pitch.append(getattr(msg, which).orientationNED.y)
      elif which == "carParams":
        op_long |= msg.carParams.openpilotLongitudinalControl
  if op_long:
    print("WARNING: openpilot longitudinal was on in some of these logs, ACC_CMD there is openpilot's, not the camera's")
  return t_cmd, req, gas, brake, t_cs, a_ego, v_ego, t_pose, pitch


def analyze(req, gas, brake, accel, v, sin_pitch):
  is_gas, is_brake = req == GAS_REQ, req == BRAKE_REQ
  print(f"{len(req)} commands: {int(is_gas.sum())} gas, {int(is_brake.sum())} brake")
  results = {}
  if is_gas.sum() > 50:
    results["gas"] = fit_linear(accel[is_gas], v[is_gas], sin_pitch[is_gas], gas[is_gas])
    print("GAS_CMD   =", results["gas"])
    a = results["gas"].coef[0]
    if abs(a) > 1e-6:
      print(f"  grade check: sin(pitch) coef / accel coef = {results['gas'].coef[2] / a:.2f} (gravity would be ~{9.81:.2f})")
  if is_brake.sum() > 50:
    results["brake"] = fit_linear(accel[is_brake], v[is_brake], sin_pitch[is_brake], brake[is_brake])
    print("BRAKE_CMD =", results["brake"])
  sel = is_gas | is_brake
  if sel.sum() > 50:
    th, agree = best_threshold(accel[sel], is_gas[sel])
    results["threshold"] = (th, agree)
    print(f"gas/brake threshold: accel > {th:+.2f} m/s^2 -> gas  ({agree * 100:.1f}% agreement with the camera)")
  return results


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("paths", nargs="+", help="rlog files, directories with rlogs, or route identifiers")
  parser.add_argument("--lag", type=float, default=0.3, help="seconds between the command and the resulting accel")
  args = parser.parse_args()

  paths = []
  for p in args.paths:
    if os.path.isdir(p):
      paths += sorted(glob.glob(os.path.join(p, "**", "rlog*"), recursive=True))
    else:
      paths.append(p)

  t_cmd, req, gas, brake, t_cs, a_ego, v_ego, t_pose, pitch = read_logs(paths)
  if not t_cmd or not t_cs:
    raise SystemExit("no ACC_CMD or carState found")
  samples = build_samples(t_cmd, req, gas, brake, t_cs, a_ego, v_ego, t_pose, pitch, args.lag)
  analyze(*samples)
  if not len(t_pose):
    print("note: no deviceMotion/livePose in logs, the grade term could not be fitted")


if __name__ == "__main__":
  main()
