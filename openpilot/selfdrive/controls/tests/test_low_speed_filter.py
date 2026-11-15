import numpy as np

from openpilot.common.constants import CV
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.low_speed_filter import LOW_SPEED_FILTER_TAU, LowSpeedCurvatureFilter

KMH = CV.KPH_TO_MS


def run(signal, v_ego, active=True):
  f = LowSpeedCurvatureFilter()
  return np.array([f.update(k, v_ego, active) for k in signal])


def test_nothing_changes_from_30_kmh():
  signal = np.sin(np.arange(1000) * .1) * .01
  assert np.array_equal(run(signal, 30 * KMH), signal)
  assert np.array_equal(run(signal, 100 * KMH), signal)


def test_wobble_shrinks_in_traffic():
  t = np.arange(0, 20, DT_CTRL)
  signal = .003 * np.sin(2 * np.pi * t / 1.5)
  assert run(signal, 10 * KMH)[500:].std() < .2 * signal.std()


def test_filter_fades_between_15_and_30_kmh():
  t = np.arange(0, 20, DT_CTRL)
  signal = .003 * np.sin(2 * np.pi * t / 1.5)
  swing = [run(signal, v * KMH)[500:].std() for v in (15, 20, 25, 29)]
  assert swing == sorted(swing)
  assert swing[-1] < signal.std()


def test_a_corner_gets_through():
  signal = np.full(int(6 / DT_CTRL), .03)
  out = run(signal, 10 * KMH)
  assert abs(out[int(LOW_SPEED_FILTER_TAU / DT_CTRL)] - .03 * (1 - np.exp(-1))) < .001
  assert out[-1] > .95 * .03


def test_follows_the_car_when_not_steering():
  # engaging must start from the current curvature, with nothing left from earlier
  f = LowSpeedCurvatureFilter()
  f.update(.05, 10 * KMH, False)
  assert f.curvature == .05
  assert abs(f.update(.05, 10 * KMH, True) - .05) < 1e-9


def test_no_lag_builds_up_at_speed():
  f = LowSpeedCurvatureFilter()
  for _ in range(100):
    f.update(.02, 40 * KMH, True)
  assert f.update(-.02, 40 * KMH, True) == -.02
