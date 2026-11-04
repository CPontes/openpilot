#!/usr/bin/env bash

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1

# models get lower priority than ui
# - ui is ~5ms
# - modeld is 20ms
# - DM is 10ms
# in order to run ui at 60fps (16.67ms), we need to allow
# it to preempt the model workloads. we have enough
# headroom for this until ui is moved to the CPU.
export QCOM_PRIORITY=12

if [ -z "$AGNOS_VERSION" ]; then
  export AGNOS_VERSION="20.1"
fi

# LizPilot (generic edition): openpilot recognizes the car by itself, as in stock openpilot.
# A car it can't fingerprint yet (the GWM Haval H6 GT, whose FW versions aren't in opendbc) is named in
# /data/lizpilot/fingerprint, e.g. GWM_HAVAL_H6. Only letters, digits and "_" are kept.
if [ -f /data/lizpilot/fingerprint ]; then
  LIZPILOT_FINGERPRINT="$(head -c 64 /data/lizpilot/fingerprint | tr -cd 'A-Z0-9_')"
  if [ -n "$LIZPILOT_FINGERPRINT" ]; then
    export FINGERPRINT="$LIZPILOT_FINGERPRINT"
  fi
fi

export STAGING_ROOT="/data/safe_staging"
