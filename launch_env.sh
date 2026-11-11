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

# The edition is one word in openpilot/selfdrive/hexa/edition.txt (edition.py): the only thing that differs between the
# Haval build (CPontes/openpilot h6gt) and LizPilot for every car (CPontes/lizpilot).
LIZPILOT_EDITION_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/openpilot/selfdrive/hexa/edition.txt"
# byte by byte (LC_ALL=C), as edition.py reads it: the two always agree, whatever the file holds
if [ "$(head -c 16 "$LIZPILOT_EDITION_FILE" 2>/dev/null | LC_ALL=C tr -cd 'a-z0-9')" = "generic" ]; then
  # openpilot recognizes the car by itself, as in stock openpilot. A car it can't fingerprint yet (the GWM Haval H6 GT,
  # whose FW versions aren't in opendbc) is named in /data/lizpilot/fingerprint, e.g. GWM_HAVAL_H6. Only letters,
  # digits and "_" are kept.
  if [ -f /data/lizpilot/fingerprint ]; then
    LIZPILOT_FINGERPRINT="$(head -c 64 /data/lizpilot/fingerprint | LC_ALL=C tr -cd 'A-Z0-9_')"
    if [ -n "$LIZPILOT_FINGERPRINT" ]; then
      export FINGERPRINT="$LIZPILOT_FINGERPRINT"
    fi
  fi
else
  # GWM Haval H6 GT PHEV: force the fingerprint until this car's FW versions are added.
  # The FW query still runs (needs comma power on the OBD port) so carFw/VIN get logged.
  export FINGERPRINT=GWM_HAVAL_H6
fi

export STAGING_ROOT="/data/safe_staging"
