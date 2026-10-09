# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Command-line screening of one site.

Usage:  python analyze.py LAT LON [--mode dam|harvest] [--height H] [--area 20] [--out result.json]
"""
import argparse
import json
import logging

from app.analysis.pipeline import run_analysis

ap = argparse.ArgumentParser()
ap.add_argument("lat", type=float)
ap.add_argument("lon", type=float)
ap.add_argument("--mode", default="dam", choices=["dam", "harvest"])
ap.add_argument("--height", type=float, default=None)
ap.add_argument("--out", default=None)
a = ap.parse_args()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

r = run_analysis(a.lon, a.lat, mode=a.mode, dam_height=a.height)
A = r["assessment"]
print(f"\nSCORE {A['score']} / 100  —  {A['rating']}  (data confidence {A['data_confidence']}%)")
for f in A["factors"]:
    s = "n/a" if f["score"] is None else f"{f['score']:5.1f}"
    print(f"  {f['label']:32s} w={f['weight']:2d}  {s}  {f['plain']['metric']}")
for fl in A["flags"]:
    print(f"  [{fl['level']}] {fl['text']}")
print(f"\nCatchment {r['catchment']['area_km2']:.1f} km²; site {r['site']['lat']:.5f}, {r['site']['lon']:.5f}")
if r.get("water_bodies"):
    w = r["water_bodies"]
    print(f"Water bodies inside the catchment: {w['count']} ({w['total_area_km2']:.3f} km²)")
print("Data:", json.dumps(r["data_status"], indent=1))
if a.out:
    with open(a.out, "w") as fh:
        json.dump(r, fh)
    print("Saved", a.out)
