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

# GWM Haval H6 GT PHEV: force the fingerprint until this car's FW versions are added.
# The FW query still runs (needs comma power on the OBD port) so carFw/VIN get logged.
export FINGERPRINT=GWM_HAVAL_H6

export STAGING_ROOT="/data/safe_staging"
