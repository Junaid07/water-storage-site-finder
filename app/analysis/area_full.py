# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Second stage of the area search: run the FULL single-site screening on the
best quick-scan candidates, so that the ranked list shows exactly the same score
(and the same saved result) that the user sees when opening a site."""
from __future__ import annotations

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.analysis.pipeline import AnalysisError, run_analysis

log = logging.getLogger(__name__)

FULL_SNAP_M = 60          # sites come from the stream network already -> barely re-snap
SHORTLIST = 16          # candidates screened in full (some may be dropped as duplicates)
SHOW = 10                 # sites shown to the user


class SharedProviders:
    """Wraps a provider so parallel site analyses respect rate-limited services
    (SoilGrids ~5 calls/min, Overpass ~2 concurrent) and share repeated look-ups."""

    def __init__(self, inner):
        self.inner = inner
        self._soil_lock = threading.Lock()
        self._osm_lock = threading.Lock()
        self._last_soil = 0.0
        self._soil_cache = {}
        self._clim_lock = threading.Lock()
        self._clim_cache = {}

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def soil(self, lon, lat):
        key = (round(lon, 2), round(lat, 2))      # ~1 km: nearby sites share a soil query
        with self._soil_lock:
            if key in self._soil_cache:
                return self._soil_cache[key]
            wait = 12.5 - (time.time() - self._last_soil)
            if wait > 0 and getattr(self.inner, "name", "") == "online":
                time.sleep(wait)
            out = self.inner.soil(lon, lat)
            self._last_soil = time.time()
            self._soil_cache[key] = out
            return out

    def climate(self, points):
        # all sites in an area mostly share the same rounded rainfall point: fetch once
        key = tuple((round(x, 1), round(y, 1)) for x, y in points[:3])
        with self._clim_lock:
            if key not in self._clim_cache:
                self._clim_cache[key] = self.inner.climate(points)
            return self._clim_cache[key]

    @property
    def last_climate_note(self):
        return getattr(self.inner, "last_climate_note", None)

    def osm(self, *a, **k):
        with self._osm_lock:
            return self.inner.osm(*a, **k)


def site_summary(res):
    """Compact summary of a full result for the ranked list / map."""
    i = res.get("default_scenario", 0)
    sc = (res.get("scenarios") or [None])[i] if res.get("scenarios") else None
    A = sc["assessment"] if sc else res["assessment"]
    ch = sc["chosen"] if sc else res.get("chosen") or {}
    return dict(
        result_id=res["id"], lon=res["site"]["lon"], lat=res["site"]["lat"],
        score=A["score"], rating=A["rating"], data_confidence=A["data_confidence"],
        groups=[dict(label=g["label"], score=g["score"], verdict=g["verdict"]) for g in A["groups"]],
        flags=[f["text"] for f in A["flags"] if f["level"] in ("fatal", "warning")],
        H=ch.get("H"), volume_mcm=ch.get("volume_mcm"), area_km2=ch.get("area_km2"),
        crest_length_m=ch.get("crest_length_m"), wer=ch.get("wer"),
        catchment_km2=res["catchment"]["area_km2"],
        reservoir=sc["reservoir"] if sc else None, dam_axis=sc["dam_axis"] if sc else None,
    )


def refine_full(quick, mode, key_for, save, progress, providers=None, workers=3):
    """quick: output of scan_area. key_for(lon, lat) -> result id used by /api/analyze.
    save(key, result, rasters) stores a result. Returns the area result with full-scored sites."""
    cands = quick["sites"][:SHORTLIST]
    if providers is None:
        import os
        if os.environ.get("DAMFINDER_PROVIDERS") != "synthetic":
            from app.data.providers import OnlineProviders
            providers = OnlineProviders()
    shared = SharedProviders(providers) if providers is not None else None
    n = len(cands)
    done = [0]
    lock = threading.Lock()

    def one(c):
        key = key_for(c["lon"], c["lat"])
        res, ras = run_analysis(c["lon"], c["lat"], mode=mode, snap_radius_m=FULL_SNAP_M,
                                providers=shared, return_rasters=True, harvest_suggestions=False,
                                progress=lambda p, m: None)
        res["id"] = key
        res["input"]["snap_radius_m"] = FULL_SNAP_M
        save(key, res, ras)
        with lock:
            done[0] += 1
            progress(55 + int(42 * done[0] / max(n, 1)),
                     f"Full screening of the best candidates: {done[0]} of {n} done…")
        return site_summary(res)

    progress(55, f"Full screening of the {n} best candidates (this takes several minutes)…")
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, c) for c in cands]
        for f in as_completed(futs):
            try:
                out.append(f.result())
            except AnalysisError as exc:
                log.info("candidate skipped: %s", exc)
            except Exception as exc:
                log.warning("candidate failed: %s", exc)
    # two candidates may snap to the same place: keep the better one
    out.sort(key=lambda d: -d["score"])
    from shapely.geometry import Point, shape
    uniq, lakes = [], []
    for d in out:
        if not all(math.hypot(d["lon"] - u["lon"], d["lat"] - u["lat"]) > 0.0015 for u in uniq):
            continue
        p = Point(d["lon"], d["lat"])
        if any(lk.buffer(0.0005).contains(p) for lk in lakes):   # would be drowned by a better-ranked reservoir
            continue
        uniq.append(d)
        if d.get("reservoir"):
            try:
                lakes.append(shape(d["reservoir"]["geometry"]))
            except Exception:
                pass
    for i, d in enumerate(uniq[:SHOW], 1):
        d["rank"] = i
    quick = dict(quick)
    quick["quick_candidates"] = len(quick["sites"])
    quick["sites"] = uniq[:SHOW]
    quick["n_full"] = len(out)
    quick["note"] = ("Each listed site was assessed with the full screening method, so the score here is exactly the "
                     "score you see when you open the site.")
    return quick
