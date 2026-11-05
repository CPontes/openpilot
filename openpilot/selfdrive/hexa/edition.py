"""Which LizPilot this is, and which car it is in.

Two editions share the same code:
- "h6gt": the owner's GWM Haval H6 GT build (CPontes/openpilot, branch h6gt, the canal).
- "generic": CPontes/lizpilot, LizPilot for every car openpilot supports.

The edition is the one word in edition.txt, next to this file, and launch_env.sh reads it too: the generic repository is
the canal with that file changed. Everything else that differs is decided here or by the car's brand. The brand comes from CarParamsPersistent, which card.py
writes when the car is recognized. Before the first drive it is unknown, and the generic edition then treats the car as
not GWM: whatever was only tested on the Haval stays off and out of sight.
"""
import os
from pathlib import Path

EDITIONS = ("generic", "h6gt")
EDITION_FILE = Path(__file__).with_name("edition.txt")


def read_edition(path=EDITION_FILE) -> str:
  """The word in edition.txt, read exactly as launch_env.sh reads it (the first 16 bytes, only a-z and 0-9), so both
  always agree. Missing, unreadable or unknown: the Haval build, which the canal always was. Never raises."""
  try:
    word = "".join(c for c in path.read_bytes()[:16].decode("latin-1") if c in "abcdefghijklmnopqrstuvwxyz0123456789")
  except Exception:
    return "h6gt"
  return word if word in EDITIONS else "h6gt"


# The tests of the canal's code run as "h6gt", the build they were written for (tests/conftest.py), in either repository;
# test_edition.py checks the generic edition in its own process.
EDITION = os.environ["LIZPILOT_EDITION"] if os.environ.get("LIZPILOT_EDITION") in EDITIONS else read_edition()
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
