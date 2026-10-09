# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Check that every open-data source is reachable from this computer.

Usage:  python check_sources.py [lat lon]
"""
import sys
import time
import logging

logging.basicConfig(level=logging.WARNING, format="        -> %(name)s: %(message)s")

from app.data.providers import OnlineProviders  # noqa: E402
from app.data.raster_io import make_grid  # noqa: E402

lat, lon = (float(sys.argv[1]), float(sys.argv[2])) if len(sys.argv) > 2 else (33.72, 73.05)
P = OnlineProviders()
g = make_grid(lon, lat, 3000, 30)


def check(name, fn):
    t = time.time()
    try:
        out = fn()
        ok = out is not None and (not hasattr(out, "__len__") or len(out) > 0)
        print(f"[{'OK ' if ok else 'FAIL'}] {name:38s} {time.time() - t:5.1f}s  {summary(out)}")
    except Exception as exc:
        print(f"[FAIL] {name:38s} {time.time() - t:5.1f}s  {type(exc).__name__}: {exc}")


def summary(o):
    import numpy as np
    if o is None:
        return "no data"
    if isinstance(o, tuple):
        o = o[0]
        if o is None:
            return "no data"
    if isinstance(o, np.ndarray):
        return f"array {o.shape}, min {np.nanmin(o)}, max {np.nanmax(o)}"
    if isinstance(o, dict):
        return ", ".join(f"{k}={v if not hasattr(v, '__len__') or isinstance(v, str) else len(v)}" for k, v in list(o.items())[:5])
    if isinstance(o, list):
        return f"{len(o)} items"
    return str(o)[:80]


import sys as _s; print(f"Python {_s.version.split()[0]}"); print(f"Checking data sources around lat={lat}, lon={lon}\n")
check("Copernicus DEM 30 m (AWS)", lambda: P.dem(g))
check("ESA WorldCover 2021 (AWS)", lambda: P.worldcover(g))
gbig = make_grid(lon, lat, 12000, 30)
check("DEM, full 24 km analysis window", lambda: P.dem(gbig))
check("WorldCover, full 24 km window", lambda: P.worldcover(gbig))
check("JRC Surface Water (Planetary Comp.)", lambda: P.jrc_occurrence(g))
check("Surface water with fallback", lambda: P.surface_water(g))
if len(sys.argv) <= 2:
    check("JRC at Rawal Lake (max should be ~100)", lambda: P.jrc_occurrence(make_grid(73.12, 33.70, 2000, 30)))
check("ESA CCI land cover 1992 (Plan. Comp.)", lambda: P.cci_landcover(make_grid(lon, lat, 3000, 300), 1992))
check("ERA5 rainfall/ET0 (Open-Meteo)", lambda: P.climate([(lon, lat)]))
check("SoilGrids (ISRIC)", lambda: P.soil(lon, lat))
check("USGS earthquakes", lambda: P.earthquakes(lon, lat))
check("OpenStreetMap (Overpass)", lambda: P.osm(lon, lat, (lon - 0.03, lat - 0.03, lon + 0.03, lat + 0.03)))
print("\nAny FAIL means that dataset will be skipped; the score is then computed from the rest.")
