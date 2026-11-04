"""Which LizPilot this is, and which car it is in.

Two editions share the same code:
- "h6gt": the owner's GWM Haval H6 GT build (CPontes/openpilot, branch h6gt, the canal).
- "generic": CPontes/lizpilot, LizPilot for every car openpilot supports.

Everything that differs between them is decided here or by the car's brand, so the generic edition can keep following
the canal (tools/lizpilot/sync_canal.sh) without touching it. The brand comes from CarParamsPersistent, which card.py
writes when the car is recognized. Before the first drive it is unknown, and the generic edition then treats the car as
not GWM: whatever was only tested on the Haval stays off and out of sight.
"""
import os

EDITIONS = ("generic", "h6gt")
# This repository is the generic edition. The tests of the canal's code run as "h6gt", the build they were written for
# (tests/conftest.py); test_edition.py checks this edition in its own process.
EDITION = os.environ.get("LIZPILOT_EDITION", "generic") if os.environ.get("LIZPILOT_EDITION") in EDITIONS else "generic"
GWM_ONLY = ("gwm",)  # options and missions measured and tuned on the Haval only

_cache: tuple[bytes | None, str | None] = (None, None)


def car_brand(params) -> str | None:
  """The brand of the car this comma was last in ("gwm", "toyota", ...), or None if unknown. Never raises."""
  global _cache
  try:
    raw = params.get("CarParamsPersistent")
  except Exception:
    return None
  if not isinstance(raw, (bytes, bytearray)) or not raw:
    return None
  raw = bytes(raw)
  if _cache[0] == raw:
    return _cache[1]
  try:
    from opendbc.car.structs import car
    with car.CarParams.from_bytes(raw) as cp:
      brand = str(cp.brand) or None
  except Exception:
    brand = None
  _cache = (raw, brand)
  return brand


def allows(brands: tuple[str, ...], params) -> bool:
  """True when something limited to these brands may be offered in this car. No limit: always."""
  if not brands:
    return True
  brand = car_brand(params)
  if brand is None:
    return EDITION != "generic"  # the Haval build is only installed in the Haval
  return brand in brands
