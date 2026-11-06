#!/usr/bin/env python3
"""How much of a drive had a speed limit on the map (CRUISE-03): run on the rlogs of drives made with "limite da via pelo
mapa" on, so mapdOut is in them.

  python tools/lizpilot/cobertura_limite.py <rlog.zst>...

Prints, per file and in total: km driven, km with the map loaded, km with a limit, and the limits seen (km/h: km).
The rlogs stay where they are (they carry the drive's location): nothing is written.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from openpilot.selfdrive.hexa.timeline import read_rlog


def coverage(events) -> dict:
  v, limit, tile = 0., 0., False
  last_t = None
  out = {"km": 0., "km_tile": 0., "km_limit": 0., "limits": Counter()}
  for ev in sorted(events, key=lambda e: e.logMonoTime):
    w = ev.which()
    if w == "mapdOut":
      limit, tile = float(ev.mapdOut.speedLimit), bool(ev.mapdOut.tileLoaded)
    elif w == "carState":
      t = ev.logMonoTime / 1e9
      if last_t is not None and 0 < t - last_t < 1:
        km = v * (t - last_t) / 1000
        out["km"] += km
        out["km_tile"] += km if tile else 0.
        if tile and limit > 0:
          out["km_limit"] += km
          out["limits"][round(limit * 3.6)] += km
      v, last_t = float(ev.carState.vEgo), t
  return out


def main(paths) -> None:
  total = {"km": 0., "km_tile": 0., "km_limit": 0., "limits": Counter()}
  for p in paths:
    c = coverage(read_rlog(p))
    for k in ("km", "km_tile", "km_limit"):
      total[k] += c[k]
    total["limits"].update(c["limits"])
    pct = 100 * c["km_limit"] / c["km"] if c["km"] else 0
    print(f"{Path(p).name}: {c['km']:.2f} km, mapa {c['km_tile']:.2f} km, com limite {c['km_limit']:.2f} km ({pct:.0f}%)")
  pct = 100 * total["km_limit"] / total["km"] if total["km"] else 0
  print(f"TOTAL: {total['km']:.1f} km, com limite no mapa {total['km_limit']:.1f} km ({pct:.0f}%)")
  print("limites:", ", ".join(f"{k} km/h: {v:.1f} km" for k, v in sorted(total["limits"].items())))


if __name__ == "__main__":
  if len(sys.argv) < 2:
    sys.exit(__doc__)
  main(sys.argv[1:])
