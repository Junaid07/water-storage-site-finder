# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Area-of-interest scan: find and rank potential storage-dam or water-harvesting
sites inside a user polygon. This is a fast first pass; any candidate can then be
opened in the full single-site screening."""
from __future__ import annotations

import logging
import math
import time

import numpy as np
from rasterio import features
from rasterio.transform import Affine
from scipy import ndimage
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform  # noqa: F401  (kept for older shapely)
import shapely

from app.analysis import hydrology as H
from app.analysis import landcover as LC
from app.analysis.geom import mask_to_geojson, rc_lines_to_geojson
from app.analysis.hydro import FlowGrid
from app.analysis.site import MODE_PARAMS, _abutments, axis_direction, cross_section
from app.data.raster_io import Grid, make_grid_for_bounds

log = logging.getLogger(__name__)
from app.config import MAX_AOI_KM2  # noqa: E402  (500 km² online, 3,000 km² on your own computer)


class AreaError(Exception):
    pass


def _ramp(x, xs, ys):
    return float(np.interp(x, xs, ys)) if x is not None and np.isfinite(x) else 0.0


def aoi_area_km2(geom_wgs84):
    from pyproj import Geod
    return abs(Geod(ellps="WGS84").geometry_area_perimeter(geom_wgs84)[0]) / 1e6


def quick_site(dem, fg: FlowGrid, r, c, mode, heights, res):
    """Fast dam/structure evaluation using a local window and an upstream half-plane
    instead of a full catchment mask."""
    p = MODE_PARAMS[mode]
    v, perp = axis_direction(fg, r, c)
    s, z, rows, cols = cross_section(dem, r, c, perp, max_cells=int((3000 if mode == "dam" else 700) / res))
    win = dem[max(0, r - 1):r + 2, max(0, c - 1):c + 2]
    z0 = float(np.nanmin(win))
    W = int((6000 if mode == "dam" else 1500) / res)
    h, w = dem.shape
    r0, r1, c0, c1 = max(0, r - W), min(h, r + W + 1), max(0, c - W), min(w, c + W + 1)
    dw = dem[r0:r1, c0:c1]
    rr, cc = np.indices(dw.shape)
    up = ((rr + r0 - r) * v[0] + (cc + c0 - c) * v[1]) <= 0.5      # upstream side of the axis
    lr, lc_ = r - r0, c - c0
    best, curve = None, []
    for Hh in heights:
        zc = z0 + Hh
        li, ri = _abutments(s, z, zc)
        if li is None or ri is None:
            break
        fsl = zc - p["freeboard"](Hh)
        wet = (dw <= fsl) & up
        lab, _ = ndimage.label(wet, structure=np.ones((3, 3), bool))
        ids = np.unique(lab[max(0, lr - 1):lr + 2, max(0, lc_ - 1):lc_ + 2])
        ids = ids[ids > 0]
        if not ids.size:
            continue
        m = np.isin(lab, ids)
        edge = bool(m[0].any() or m[-1].any() or m[:, 0].any() or m[:, -1].any())
        area = float(m.sum() * res * res)
        vol = float(np.nansum(np.where(m, fsl - dw, 0)) * res * res)
        d = np.nan_to_num(np.clip(zc - z[li:ri + 1], 0, None))
        crest_w = 3.0 + Hh / 5.0
        fill = float((crest_w * d + 0.5 * (p["us"] + p["ds"]) * d ** 2).sum() * (s[1] - s[0]) * res)
        e = dict(H=Hh, crest_length_m=float((s[ri] - s[li]) * res), fill_m3=fill, area_km2=area / 1e6,
                 volume_mcm=vol / 1e6, wer=vol / fill if fill > 0 else 0.0, edge=edge,
                 _mask=m, _win=(r0, c0), _axis=(rows[[li, ri]], cols[[li, ri]]))
        curve.append(e)
        if edge:
            break
    return curve


def scan_area(aoi_geojson, mode="dam", providers=None, progress=None, max_sites=25):
    prog = progress or (lambda p, m: log.info("%3d%% %s", p, m))
    t0 = time.time()
    if providers is None:
        import os
        if os.environ.get("DAMFINDER_PROVIDERS") == "synthetic":
            from tests.synthetic import SyntheticProviders
            g_ = shape(aoi_geojson)
            providers = SyntheticProviders(lon0=g_.centroid.x, lat0=g_.centroid.y)
        else:
            from app.data.providers import OnlineProviders
            providers = OnlineProviders()
    P = providers
    mode = "harvest" if mode == "harvest" else "dam"
    geom = shape(aoi_geojson)
    if geom.is_empty:
        raise AreaError("The area is empty.")
    geom = geom.buffer(0)
    area = aoi_area_km2(geom)
    if area > MAX_AOI_KM2:
        raise AreaError(f"The area is {area:,.0f} km². Please draw or upload an area smaller than {MAX_AOI_KM2:,.0f} km².")
    if area < 0.2:
        raise AreaError("The area is too small (< 0.2 km²).")
    res = 30.0 if area <= 800 else 60.0
    buf = 2000 if mode == "dam" else 600
    g = make_grid_for_bounds(geom.bounds, res, buffer_m=buf)
    prog(5, f"Downloading terrain for {area:,.0f} km² ({g.width}×{g.height} cells at {res:.0f} m)…")
    from app.data.providers import TerrainDownloadError
    try:
        dem, dem_src = P.dem(g)
    except TerrainDownloadError as exc:
        raise AreaError(str(exc)) from exc
    if dem is None:
        raise AreaError("No elevation data available for this area.")
    prog(20, "Computing flow directions and stream network…")
    fg = FlowGrid(dem, res)
    acc = fg.acc_km2
    # AOI mask on the grid
    to_utm = g.from_wgs84()
    geom_utm = shapely.transform(geom, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])))
    aoi = features.rasterize([(mapping(geom_utm), 1)], out_shape=g.shape, transform=g.transform, fill=0,
                             dtype="uint8").astype(bool)

    prog(30, "Fetching rainfall and land cover…")
    c_lon, c_lat = geom.centroid.x, geom.centroid.y
    clim = P.climate([(c_lon, c_lat)])
    wc = P.worldcover(g)
    lc_frac = LC.worldcover_fractions(wc, aoi) if wc is not None else {}
    # existing lakes / reservoirs: candidates on them would just rediscover an existing dam
    water = None
    try:
        sw = P.surface_water(g, wc, res <= 30)
        occ = sw[0] if sw else None
        water = np.zeros(g.shape, bool)
        if occ is not None:
            water |= (occ != 255) & (occ >= 50)
        if wc is not None:
            water |= wc == 80
        wlab, _ = ndimage.label(water, structure=np.ones((3, 3), bool))
        wsize = np.bincount(wlab.ravel()) * res * res / 1e6
        wsize[0] = 0
        big = wsize[wlab] >= (0.01 if mode == "dam" else 0.002)
        near_water = ndimage.binary_dilation(big, iterations=max(1, int(round((150 if mode == "dam" else 60) / res))))
        water = big
    except Exception as exc:
        log.warning("surface water for area scan failed: %s", exc)
        water = near_water = None
    n_existing = 0
    soil = P.soil(c_lon, c_lat)
    hsg, _ = H.hydrologic_soil_group(soil)
    cn = H.composite_cn(lc_frac, hsg) if lc_frac else 75.0
    mar_mm = quick_mm = pet_mm = None
    stats = None
    if clim is not None:
        stats = H.climate_stats(clim)
        pet_mm = stats["mean_annual_pet_mm"]
        wy = H.water_yield(clim, stats, 1.0, cn)
        mar_mm, quick_mm = wy["mean_annual_runoff_mm"], wy["scs_quickflow_mm"]

    # ---------------- candidate cells
    prog(40, "Finding candidate sites along the streams…")
    if mode == "dam":
        amin, amax, block = 2.0, 1e9, int(round(750 / res))
        heights = [5, 10, 15, 20, 30, 40, 50]
    else:
        amin, amax, block = 0.05, 5.0, int(round(300 / res))
        heights = [1.5, 2, 3, 4, 5]
    ok = aoi & (acc >= amin) & (acc <= amax)
    cand = []
    h, w = acc.shape
    a_ok = np.where(ok, acc, -1)
    for br in range(0, h, block):
        for bc in range(0, w, block):
            sub = a_ok[br:br + block, bc:bc + block]
            k = np.argmax(sub)
            if sub.flat[k] > 0:
                rr_, cc_ = divmod(int(k), sub.shape[1])
                cand.append((br + rr_, bc + cc_))
    if len(cand) > 450:
        rng = np.random.default_rng(0)
        cand = [cand[i] for i in sorted(rng.choice(len(cand), 450, replace=False))]
    if not cand:
        raise AreaError("No suitable stream channels were found inside this area "
                        + ("(catchments ≥ 2 km² are needed for a storage dam)." if mode == "dam"
                           else "(small gullies of 0.05–5 km² are needed for water harvesting)."))

    # ---------------- evaluate
    target = MODE_PARAMS[mode]["storage_target"]

    def evap_loss(e):
        return pet_mm / 1000 * e["area_km2"] * 0.7 / max(e["volume_mcm"], 1e-9)

    results = []
    n = len(cand)
    for i, (r, c) in enumerate(cand):
        if i % 25 == 0:
            prog(40 + int(45 * i / n), f"Testing candidate sites ({i}/{n})…")
        if near_water is not None and near_water[r, c]:
            n_existing += 1
            continue
        a = float(acc[r, c])
        inflow = (mar_mm if mode == "dam" else quick_mm)
        inflow_mcm = inflow * a / 1000 if inflow else None
        try:
            curve = quick_site(dem, fg, r, c, mode, heights, res)
        except Exception as exc:  # pragma: no cover
            log.debug("candidate failed: %s", exc)
            continue
        vmin = 0.05 if mode == "dam" else 0.001       # 50,000 m³ dam / 1,000 m³ pond
        curve = [e for e in curve if e["volume_mcm"] >= vmin]
        if not curve:
            continue
        ok_e = [e for e in curve if inflow_mcm is None or e["volume_mcm"] <= target * inflow_mcm] or curve[:1]
        e = max(ok_e, key=lambda x: min(x["wer"], 40) * (1 + 0.3 * math.log10(1 + x["volume_mcm"] * 1000)))
        builtup = 0.0
        if water is not None:
            r0, c0 = e["_win"]
            m = e["_mask"]
            wsub = water[r0:r0 + m.shape[0], c0:c0 + m.shape[1]]
            if (wsub & m).sum() >= 0.5 * max(m.sum(), 1):      # reservoir mostly existing water
                n_existing += 1
                continue
        if wc is not None:
            r0, c0 = e["_win"]
            m = e["_mask"]
            builtup = float(((wc[r0:r0 + m.shape[0], c0:c0 + m.shape[1]] == 50) & m).sum() * res * res / 1e6)
        si = e["volume_mcm"] / inflow_mcm if inflow_mcm else None
        if mode == "dam":
            s_in = _ramp(math.log10(max(inflow_mcm or 1e-4, 1e-4)), [-2, -1, 0, 1, 2], [10, 40, 70, 90, 100])
            s_wer = _ramp(e["wer"], [1, 3, 10, 30, 100], [5, 25, 60, 85, 100])
            s_val = _ramp(e["crest_length_m"] / e["H"], [3, 10, 30, 100, 300], [100, 85, 60, 30, 5])
            s_si = _ramp(si, [0.05, 0.2, 0.5, 1.0, 2.0, 4.0], [30, 60, 90, 100, 70, 30]) if si is not None else 50
            s_soc = _ramp(builtup, [0, 0.01, 0.05, 0.2, 1], [100, 80, 50, 20, 0])
            s_vol = _ramp(math.log10(max(e["volume_mcm"], 1e-4)), [math.log10(0.05), -1, 0, 1, 2], [20, 40, 70, 90, 100])
            s_ev = _ramp(evap_loss(e), [0.05, 0.15, 0.3, 0.6, 1.0], [100, 75, 45, 15, 5]) if pet_mm else 60
            score = (0.22 * s_in + 0.25 * s_wer + 0.10 * s_val + 0.08 * s_vol + 0.10 * s_si + 0.12 * s_soc
                     + 0.13 * s_ev)
        else:
            s_size = _ramp(math.log10(max(a, 1e-3)), [-2, math.log10(0.05), math.log10(0.5), math.log10(5), math.log10(25), 2],
                           [40, 90, 100, 80, 35, 10])
            s_run = _ramp(quick_mm, [5, 20, 50, 100, 200], [10, 40, 70, 90, 100]) if quick_mm else 50
            s_wer = _ramp(e["wer"], [1, 2, 5, 10, 30], [10, 30, 60, 85, 100])
            gy, gx = np.gradient(dem[max(0, r - 8):r + 9, max(0, c - 8):c + 9], res)
            slope = float(np.nanmean(np.hypot(gx, gy)) * 100)
            s_slope = _ramp(slope, [0.5, 2, 5, 15, 30, 50], [40, 80, 100, 80, 40, 10])
            s_si = _ramp(si, [0.05, 0.2, 0.5, 1, 2], [50, 80, 100, 80, 50]) if si is not None else 50
            s_vol = _ramp(e["volume_mcm"] * 1e6, [1000, 5000, 20000, 100000], [20, 60, 90, 100])
            s_ev = _ramp(evap_loss(e), [0.1, 0.25, 0.5, 1.0, 2.0], [100, 65, 30, 10, 0]) if pet_mm else 60
            s_depth = _ramp(e["volume_mcm"] / max(e["area_km2"], 1e-9), [0.5, 1, 2, 3], [10, 40, 80, 100])  # mean depth m
            score = (0.12 * s_size + 0.08 * s_run + 0.18 * s_wer + 0.12 * s_vol + 0.08 * s_slope + 0.12 * s_si
                     + 0.18 * s_ev + 0.12 * s_depth)
        if builtup > 0.2:
            score = min(score, 40)
        lon, lat = g.rc_to_lonlat(r, c)
        results.append(dict(lon=lon, lat=lat, r=r, c=c, catchment_km2=a, H=e["H"], volume_mcm=e["volume_mcm"],
                            area_km2=e["area_km2"], crest_length_m=e["crest_length_m"], fill_m3=e["fill_m3"],
                            wer=e["wer"], inflow_mcm=inflow_mcm, storage_inflow=si, builtup_km2=builtup,
                            window_edge=e["edge"], score=round(score, 1), _e=e))

    # ---------------- non-maximum suppression & ranking
    prog(88, "Ranking sites…")
    results.sort(key=lambda d: -d["score"])
    sep = (1500 if mode == "dam" else 400) / res
    kept = []
    for d in results:
        if all(math.hypot(d["r"] - k["r"], d["c"] - k["c"]) >= sep for k in kept):
            kept.append(d)
        if len(kept) >= max_sites:
            break

    sites = []
    for i, d in enumerate(kept, 1):
        e = d.pop("_e")
        r0, c0 = e["_win"]
        m = e["_mask"]
        t = g.transform
        sub = Grid(g.crs, t * Affine.translation(c0, r0), m.shape[1], m.shape[0], res)
        rows, cols = e["_axis"]
        ax_lon, ax_lat = g.to_wgs84().transform(t.c + (np.asarray(cols) + 0.5) * t.a, t.f + (np.asarray(rows) + 0.5) * t.e)
        d.pop("r"), d.pop("c")
        d["rank"] = i
        d["rating"] = ("High potential" if d["score"] >= 75 else "Moderate potential" if d["score"] >= 55
                       else "Low potential" if d["score"] >= 35 else "Not recommended")
        d["reservoir"] = mask_to_geojson(m, sub, 0.6, dict(rank=i))
        d["dam_axis"] = dict(type="Feature", properties=dict(rank=i),
                             geometry=dict(type="LineString", coordinates=[[float(ax_lon[0]), float(ax_lat[0])],
                                                                            [float(ax_lon[1]), float(ax_lat[1])]]))
        sites.append(d)

    thr = max(0.5 if mode == "dam" else 0.05, 0.002 * area)
    streams = rc_lines_to_geojson(fg.stream_lines(aoi, thr / (res * res / 1e6), max_lines=3000), g, dict(name="Streams"))
    out = dict(
        mode=mode, aoi=dict(type="Feature", geometry=mapping(geom), properties={}), aoi_area_km2=area,
        resolution_m=res, n_candidates=len(cand), n_evaluated=len(results), n_existing_water=n_existing, sites=sites, streams=streams,
        climate=None if stats is None else dict(mean_annual_precip_mm=stats["mean_annual_precip_mm"],
                                                 mean_annual_pet_mm=stats["mean_annual_pet_mm"],
                                                 climate_class=stats["climate_class"]),
        runoff=dict(mean_annual_runoff_mm=mar_mm, scs_quickflow_mm=quick_mm, curve_number=cn),
        data=dict(terrain=dem_src, rainfall="ERA5 via Open-Meteo" if clim else "unavailable",
                  land_cover="ESA WorldCover 2021" if wc is not None else "unavailable"),
        elapsed_s=round(time.time() - t0, 1),
        note=("Quick scan: each site is tested with a simplified reservoir model. "
              "Open a site for the full screening and PDF report."),
    )
    prog(100, "Done")
    from app.analysis.pipeline import _clean
    return _clean(out)
