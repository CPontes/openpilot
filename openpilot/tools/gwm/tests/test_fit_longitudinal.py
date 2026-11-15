import numpy as np

from openpilot.tools.gwm.fit_longitudinal import GAS_REQ, BRAKE_REQ, analyze, build_samples


def test_recovers_known_law():
  rng = np.random.default_rng(0)
  lag, n = 0.3, 20000
  t = np.arange(n) * 0.02
  v = 10 + 5 * np.sin(t / 30)
  pitch = 0.03 * np.sin(t / 50)
  accel = 1.2 * np.sin(t / 7)  # accel the car reaches, lag seconds after the command

  t_cmd = t - lag
  is_gas = accel > -0.5
  gas = np.where(is_gas, 1200 * accel + 6 * v + 11800 * np.sin(pitch) + 1100 + rng.normal(0, 50, n), 0)
  brake = np.where(~is_gas, -18 * accel - 0.1 * v + 2 + rng.normal(0, 1, n), 0)
  req = np.where(is_gas, GAS_REQ, BRAKE_REQ)

  samples = build_samples(t_cmd, req, gas, brake, t, accel, v, t, pitch, lag)
  res = analyze(*samples)

  np.testing.assert_allclose(res["gas"].coef, [1200, 6, 11800, 1100], rtol=0.05, atol=5)
  np.testing.assert_allclose(res["brake"].coef[:2], [-18, -0.1], rtol=0.05, atol=0.05)
  assert res["gas"].r2 > 0.9 and res["brake"].r2 > 0.9
  th, agree = res["threshold"]
  assert abs(th + 0.5) < 0.05 and agree > 0.99
