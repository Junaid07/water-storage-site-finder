# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""End-to-end screening analysis for a clicked location."""
from __future__ import annotations

import logging
import math
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy import ndimage

from app.config import CATCHMENT_LEVELS
from app.analysis import hydrology as H
from app.analysis import landcover as LC
from app.analysis import sediment as SED
from app.analysis.context import osm_context, seismic_summary, water_bodies
from app.analysis.geom import (frac_rc_to_lonlat, labels_to_geojson, mask_to_geojson,
                               rc_lines_to_geojson)
from app.analysis.hydro import FlowGrid, slope_percent
from app.analysis.scoring import score
from app.analysis.site import analyse_site, choose_height, suggest_harvest_sites
from app.data.raster_io import make_grid

log = logging.getLogger(__name__)
VERSION = "1.6"


class AnalysisError(Exception):
    pass


def _touches_edge(mask, margin=2):
    return bool(mask[:margin, :].any() or mask[-margin:, :].any()
                or mask[:, :margin].any() or mask[:, -margin:].any())


def _clean(o):
    """Make result JSON-serialisable (numpy -> python, drop private keys, NaN -> None)."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return v if math.isfinite(v) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _grid_meta(g):
    return dict(crs=g.crs.to_wkt(), transform=list(g.transform)[:6], height=g.height, width=g.width, res=g.res)


def _get_dem(P, grid):
    from app.data.providers import TerrainDownloadError
    try:
        return P.dem(grid)
    except TerrainDownloadError as exc:
        raise AnalysisError(str(exc)) from exc


def existing_reservoir(occ, wc, fg, g, rs, cs, catch, mode, osm=None):
    """Is there already a lake / reservoir at the site? Uses JRC water occurrence
    (>= 50 %) and WorldCover permanent water. Returns None or a description,
    including the outlet of that water body and the catchment draining to it."""
    water = np.zeros(g.shape, bool)
    if occ is not None:
        water |= (occ != 255) & (occ >= 50)
    if wc is not None:
        water |= wc == 80
    if not water.any():
        return None
    lab, n = ndimage.label(water, structure=np.ones((3, 3), bool))
    rad = int(round((150 if mode == "dam" else 60) / g.res))
    r0, r1, c0, c1 = max(0, rs - rad), rs + rad + 1, max(0, cs - rad), cs + rad + 1
    ids = np.unique(lab[r0:r1, c0:c1])
    ids = ids[ids > 0]
    if not ids.size:
        return None
    cell_km2 = g.res ** 2 / 1e6
    sizes = ndimage.sum(water, lab, ids) * cell_km2
    k = int(np.argmax(sizes))
    area = float(sizes[k])
    if area < (0.01 if mode == "dam" else 0.002):          # 1 ha / 0.2 ha
        return None
    body = lab == ids[k]
    rr, cc = np.nonzero(body)
    dist = float(np.min(np.hypot(rr - rs, cc - cs)) * g.res)
    # where does the lake drain? -> cell with the largest flow accumulation on/next to it
    ring = ndimage.binary_dilation(body, iterations=2)
    acc = np.where(ring, fg.acc_km2, -1)
    orow, ocol = np.unravel_index(int(np.argmax(acc)), acc.shape)
    olon, olat = g.rc_to_lonlat(orow, ocol)
    dams = [d for d in ((osm or {}).get("existing_dams") or []) if (d.get("distance_km") or 9) <= 1.5]
    return dict(area_km2=area, distance_m=dist, outlet=dict(lon=olon, lat=olat),
                outlet_catchment_km2=float(fg.acc_km2[orow, ocol]),
                at_site=dist <= (150 if mode == "dam" else 60),
                osm_dam=(dams[0].get("name") or "unnamed dam") if dams else None,
                _mask=body)


def run_analysis(lon, lat, mode="dam", search_area_km2=None, snap_radius_m=None,
                 dam_height=None, providers=None, progress=None, return_rasters=False,
                 harvest_suggestions=True):
    if providers is None:
        if os.environ.get("DAMFINDER_PROVIDERS") == "synthetic":
            from tests.synthetic import SyntheticProviders
            providers = SyntheticProviders(lon0=lon, lat0=lat)
        else:
            from app.data.providers import OnlineProviders
            providers = OnlineProviders()
    P = providers
    prog = progress or (lambda pct, msg: log.info("%3d%% %s", pct, msg))
    t0 = time.time()
    mode = "harvest" if mode == "harvest" else "dam"
    status = {}
    R = dict(version=VERSION, input=dict(lon=lon, lat=lat, mode=mode, dam_height=dam_height),
             data_status=status, created=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()))
    pool = ThreadPoolExecutor(max_workers=6)

    def bg(name, fn, *a):
        def wrapped():
            try:
                return fn(*a)
            except Exception as exc:
                log.warning("%s failed: %s\n%s", name, exc, traceback.format_exc())
                return None
        return pool.submit(wrapped)

    # ------------------------------------------------------------ 1. local terrain
    prog(3, "Downloading 30 m terrain (Copernicus DEM)…")
    half0, res0 = CATCHMENT_LEVELS[0]
    g0 = make_grid(lon, lat, half0, res0)
    dem0, dem_src = _get_dem(P, g0)
    if dem0 is None or np.isnan(dem0).mean() > 0.9:
        raise AnalysisError("No elevation data available at this location (open ocean?).")
    status["Terrain"] = dem_src
    r_click, c_click = g0.lonlat_to_rc(lon, lat)
    if np.isnan(dem0[r_click, c_click]):
        raise AnalysisError("Selected point has no elevation data (sea / no-data).")

    f_soil = bg("soil", P.soil, lon, lat)
    f_eq = bg("earthquakes", P.earthquakes, lon, lat)
    f_wc0 = bg("worldcover_local", P.worldcover, g0)
    f_water0 = bg("surface_water", lambda: P.surface_water(g0, f_wc0.result()))

    prog(12, "Conditioning terrain and computing flow directions…")
    fg0 = FlowGrid(dem0, g0.res)
    cell0_km2 = g0.res ** 2 / 1e6
    snap_m = snap_radius_m or (500 if mode == "dam" else 200)
    min_km2 = 1.0 if mode == "dam" else 0.02
    rs, cs = fg0.snap(r_click, c_click, max(1, int(snap_m / g0.res)), min_cells=min_km2 / cell0_km2)
    site_lon, site_lat = g0.rc_to_lonlat(rs, cs)
    R["site"] = dict(lon=site_lon, lat=site_lat,
                     snapped_distance_m=float(np.hypot(rs - r_click, cs - c_click) * g0.res),
                     elevation_m=float(dem0[rs, cs]))

    # ------------------------------------------------------------ 2. catchment
    prog(22, "Delineating catchment…")
    catch0 = fg0.catchment(rs, cs)
    g, fg, catch, dem, rr, cc = g0, fg0, catch0, dem0, rs, cs
    truncated = _touches_edge(catch0)
    level = 0
    while truncated and level < len(CATCHMENT_LEVELS) - 1:
        level += 1
        half, res = CATCHMENT_LEVELS[level]
        prev_km2 = catch.sum() * g.res ** 2 / 1e6
        prog(22 + 4 * level, f"Catchment is large, extending window to {2 * half / 1000:.0f} km at {res} m…")
        g1 = make_grid(site_lon, site_lat, half, res, crs=g0.crs)
        dem1, _ = _get_dem(P, g1)
        if dem1 is None:
            break
        fg1 = FlowGrid(dem1, g1.res)
        r1, c1 = g1.lonlat_to_rc(site_lon, site_lat)
        r1, c1 = fg1.snap(r1, c1, 2, min_acc=0.8 * prev_km2 / (g1.res ** 2 / 1e6))
        catch1 = fg1.catchment(r1, c1)
        g, fg, catch, dem, rr, cc = g1, fg1, catch1, dem1, r1, c1
        truncated = _touches_edge(catch1)

    cell_km2 = g.res ** 2 / 1e6
    area_km2 = float(catch.sum() * cell_km2)
    flen = fg.flow_length_to(catch, rr, cc)
    far_rc = np.unravel_index(np.nanargmax(flen), flen.shape)
    dem_f = np.where(np.isnan(dem), np.nanmean(dem), dem)
    slope = slope_percent(dem_f, g.res)
    crow, ccol = np.argwhere(catch).mean(axis=0)
    cen_lon, cen_lat = g.rc_to_lonlat(crow, ccol)
    zc = dem[catch]
    R["catchment"] = dict(
        area_km2=area_km2, truncated=truncated, resolution_m=g.res,
        centroid=dict(lon=cen_lon, lat=cen_lat),
        elev_min_m=float(np.nanmin(zc)), elev_max_m=float(np.nanmax(zc)), elev_mean_m=float(np.nanmean(zc)),
        relief_m=float(np.nanmax(zc) - np.nanmin(zc)), mean_slope_pct=float(np.nanmean(slope[catch])),
        longest_flow_path_km=float(flen[far_rc] / 1000),
        flow_path_drop_m=float(dem[far_rc] - dem[rr, cc]),
    )
    thr_km2 = max(0.05 if mode == "harvest" else 0.5, 0.01 * area_km2)
    streams = fg.stream_lines(catch, thr_km2 / cell_km2)

    # catchment bounding box (lon/lat) for OSM
    rows, cols = np.where(catch)
    corners = [g.rc_to_lonlat(r_, c_) for r_, c_ in
               [(rows.min(), cols.min()), (rows.min(), cols.max()), (rows.max(), cols.min()), (rows.max(), cols.max())]]
    bbox = (min(p[0] for p in corners) - 0.01, min(p[1] for p in corners) - 0.01,
            max(p[0] for p in corners) + 0.01, max(p[1] for p in corners) + 0.01)
    f_osm = bg("osm", P.osm, site_lon, site_lat, bbox, 10_000, area_km2 > 2000)

    pts = [(cen_lon, cen_lat)]
    if area_km2 > 300:
        idx = np.argwhere(catch)
        rng = np.random.default_rng(0)
        for k in rng.choice(len(idx), size=4, replace=False):
            pts.append(g.rc_to_lonlat(*idx[k]))
    f_clim = bg("climate", P.climate, pts)
    f_wc = f_wc0 if g is g0 else bg("worldcover", P.worldcover, g)
    f_water = f_water0 if g is g0 else bg("surface_water_catchment",
                                          lambda: P.surface_water(g, f_wc.result(), g.res <= 90))
    f_cci_old = bg("cci1992", P.cci_landcover, g, 1992)
    f_cci_new = bg("cci2020", P.cci_landcover, g, 2020)
    io_years = (2017, 2023)
    f_io_old = bg("io_old", getattr(P, "io_landcover", lambda *a: None), g, io_years[0])
    f_io_new = bg("io_new", getattr(P, "io_landcover", lambda *a: None), g, io_years[1])

    # ------------------------------------------------------------ 3. climate & yield
    prog(40, "Fetching 30+ years of rainfall and evapotranspiration (ERA5)…")
    clim = f_clim.result()
    soil = f_soil.result()
    R["soil"] = soil
    status["Soil"] = soil["source"] if soil else "unavailable"
    hsg, hsg_note = H.hydrologic_soil_group(soil)
    prog(52, "Analysing land cover…")
    wc = f_wc.result()
    lc_frac = LC.worldcover_fractions(wc, catch) if wc is not None else {}
    status["Land cover"] = "ESA WorldCover 10 m 2021" if lc_frac else "unavailable"
    R["landcover"] = lc_frac or None
    R["landcover_named"] = [dict(code=k, name=LC.WORLDCOVER_NAMES.get(k, str(k)),
                                 color=LC.WORLDCOVER_COLORS.get(k, "#888"), pct=100 * v)
                            for k, v in sorted(lc_frac.items(), key=lambda kv: -kv[1])]
    cn = H.composite_cn(lc_frac, hsg) if lc_frac else 75.0
    R["runoff_params"] = dict(hsg=hsg, hsg_note=hsg_note, curve_number=cn)

    stats = None
    if clim is not None:
        stats = H.climate_stats(clim)
        R["climate"] = stats
        status["Climate"] = clim["source"] + f" ({stats['n_years']} yrs, {clim['n_points']} pt)"
        R["yield"] = H.water_yield(clim, stats, area_km2, cn)
        R["flood"] = H.design_flood(stats, area_km2, cn, R["catchment"]["longest_flow_path_km"] * 1000,
                                    R["catchment"]["flow_path_drop_m"])
        R["floods"] = H.flood_table(stats, area_km2, cn, R["catchment"]["longest_flow_path_km"] * 1000,
                                    R["catchment"]["flow_path_drop_m"], mode)
    else:
        note = getattr(P, "last_climate_note", None)
        status["Climate"] = "unavailable" + (f": {note}" if note else "")

    cci_old, cci_new = f_cci_old.result(), f_cci_new.result()
    if cci_old is not None and cci_new is not None:
        R["landcover_change"] = LC.change_summary(LC.cci_group_fractions(cci_old, catch),
                                                  LC.cci_group_fractions(cci_new, catch), 1992, 2020)
        status["Land-cover change"] = "ESA CCI Land Cover 300 m (1992, 2020)"
    else:
        R["landcover_change"] = None
        status["Land-cover change"] = "unavailable"
    io_old, io_new = f_io_old.result(), f_io_new.result()
    R["landcover_change_recent"] = None
    if io_old is not None and io_new is not None:
        R["landcover_change_recent"] = LC.change_summary(
            LC.cci_group_fractions(io_old, catch, LC.IO_GROUPS), LC.cci_group_fractions(io_new, catch, LC.IO_GROUPS),
            io_years[0], io_years[1], source="Impact Observatory / Esri 10 m annual land cover", detect_pp=1.0)
        status["Recent land-cover change"] = f"Impact Observatory / Esri 10 m annual LULC ({io_years[0]}, {io_years[1]})"
    else:
        status["Recent land-cover change"] = "unavailable"

    # ------------------------------------------------------------ 4. site & reservoir
    prog(62, "Testing dam heights and reservoir storage…")
    site = analyse_site(dem0, fg0, catch0, rs, cs, mode)
    inflow = None
    if R.get("yield"):
        inflow = R["yield"]["mean_annual_runoff_mcm"] if mode == "dam" else R["yield"]["scs_quickflow_mcm"]
    chosen = choose_height(site, inflow, mode, dam_height)
    R["site"]["local_slope_pct"] = site["local_slope_pct"]
    R["site"]["streambed_elev_m"] = site["z0"]
    R["site_analysis"] = dict(
        curve=[{k: v for k, v in e.items() if not k.startswith("_")} for e in site["curve"]],
        max_feasible_height_m=site["feasible"][-1]["H"] if site["feasible"] else None,
    )

    if mode == "harvest" and R.get("yield") and harvest_suggestions:
        prog(66, "Looking for small side gullies that suit water harvesting…")
        try:
            sites = suggest_harvest_sites(dem0, fg0, r_click, c_click, R["yield"]["scs_quickflow_mm"])
            for sgs in sites:
                sgs["lon"], sgs["lat"] = g0.rc_to_lonlat(sgs.pop("r"), sgs.pop("c"))
            R["harvest_sites"] = sites
        except Exception as exc:
            log.warning("harvest site search failed: %s", exc)
            R["harvest_sites"] = []

    # ------------------------------------------------------------ 5. sediment (catchment part)
    prog(70, "Estimating erosion and sediment yield (RUSLE)…")
    soil_loss = None
    sed_base = None
    if stats is not None:
        rus = SED.rusle(slope, fg.acc, g.res, wc, catch, stats["mean_annual_precip_mm"], soil)
        soil_loss = rus.pop("_A")
        sed_base = {**rus, **SED.sediment_yield(rus["mean_soil_loss_t_ha_yr"], area_km2)}

    # ------------------------------------------------------------ 6. context
    prog(78, "Mapping water bodies in the catchment, settlements, protected areas, earthquakes…")
    occ0, _src0 = f_water0.result() or (None, None)
    occ_g, water_src = f_water.result() or (None, None)
    wb = water_bodies(occ_g, g, site_lon, site_lat, catch)
    if wb:
        wb["source"] = water_src + ("" if g.res <= 90 else f" (at {g.res:.0f} m only larger lakes are resolved)")
        if occ0 is not None:
            v = int(occ0[r_click, c_click])
            wb["site_occurrence"] = None if v == 255 else v
    R["water_bodies"] = wb
    status["Surface water"] = wb["source"] if wb else "unavailable"

    def inside(plon, plat):
        pr, pc = g.lonlat_to_rc(plon, plat)
        if not (0 <= pr < g.height and 0 <= pc < g.width):
            return False
        return bool(catch[max(0, pr - 1):pr + 2, max(0, pc - 1):pc + 2].any())

    osm = osm_context(f_osm.result(), site_lon, site_lat, inside)
    R["osm"] = osm
    status["Places & protected areas"] = osm["source"] if osm else "unavailable"
    exist = None
    try:
        exist = existing_reservoir(occ0, f_wc0.result(), fg0, g0, rs, cs, catch0, mode, osm)
    except Exception as exc:
        log.warning("existing reservoir check failed: %s", exc)
    exist_mask = exist.pop("_mask") if exist else None
    R["existing_reservoir"] = exist
    seis = seismic_summary(f_eq.result(), lon, lat)
    R["seismic"] = seis
    status["Earthquakes"] = seis["source"] if seis else "unavailable"

    # ------------------------------------------------------------ 7. height scenarios
    prog(86, "Scoring every dam height…")
    wc0 = f_wc0.result()
    prof = site["profile"]
    s_m = prof["s"] * g0.res
    z = prof["z"]
    keep = ~np.isnan(z)
    step = max(1, keep.sum() // 300)
    R["cross_section"] = dict(distance_m=s_m[keep][::step].tolist(), elev_m=z[keep][::step].tolist())
    res_level = np.full(g0.shape, 255, np.uint8)
    scenarios = []
    for i, e in enumerate(site["curve"]):
        m = e["_mask"]
        res_level[m & (res_level == 255)] = i
        ec = {k: v for k, v in e.items() if not k.startswith("_")}
        soc = None
        if wc0 is not None:
            soc = dict(builtup_km2=float(((wc0 == 50) & m).sum() * cell0_km2),
                       cropland_km2=float(((wc0 == 40) & m).sum() * cell0_km2),
                       tree_km2=float(((wc0 == 10) & m).sum() * cell0_km2))
            flooded = []
            for p in (osm or {}).get("places", []):
                if p.get("lon") is None:
                    continue
                pr, pc = g0.lonlat_to_rc(p["lon"], p["lat"])
                if 0 <= pr < g0.height and 0 <= pc < g0.width and m[pr, pc]:
                    flooded.append(p["name"])
            soc["settlements_flooded"] = flooded
        if exist_mask is not None:
            soc = soc or {}
            soc["existing_water_km2"] = float((exist_mask & m).sum() * cell0_km2)
        sed = None
        if sed_base is not None:
            sed = {**sed_base, **SED.reservoir_life(e["volume_mcm"], inflow or 0, sed_base["sediment_yield_t_yr"])}
        lo, hi = e["_lr"]
        alon, alat = frac_rc_to_lonlat(g0, prof["rows"][[lo, hi]], prof["cols"][[lo, hi]])
        Rs = dict(R, chosen=ec, social=soc, sediment=sed)
        scenarios.append(dict(
            H=e["H"], chosen=ec, social=soc, sediment=sed,
            assessment=score(Rs, mode),
            reservoir=mask_to_geojson(m, g0, 0.5, dict(name=f"Reservoir at H={e['H']} m")),
            dam_axis=dict(type="Feature", properties=dict(name="Dam axis"),
                          geometry=dict(type="LineString", coordinates=[[float(alon[0]), float(alat[0])],
                                                                         [float(alon[1]), float(alat[1])]])),
            xs=dict(crest_elev=e["crest_elev"], fsl=e["fsl"], left_m=float(s_m[lo]), right_m=float(s_m[hi])),
        ))
    default_idx = next((i for i, e in enumerate(site["curve"]) if chosen is not None and e is chosen), 0)
    R["scenarios"] = scenarios
    R["default_scenario"] = default_idx
    if scenarios:
        d = scenarios[default_idx]
        R["chosen"], R["social"], R["sediment"], R["assessment"] = d["chosen"], d["social"], d["sediment"], d["assessment"]
    else:
        R["chosen"], R["social"], R["sediment"] = None, None, sed_base
        R["assessment"] = score(R, mode)

    # ------------------------------------------------------------ 8. vector layers
    prog(95, "Preparing maps…")
    layers = dict(
        catchment=mask_to_geojson(catch, g, simplify_cells=1.0, props=dict(name="Catchment")),
        streams=rc_lines_to_geojson(streams, g, dict(name="Streams")),
    )
    if wb:
        props = {b["id"]: dict(id=b["id"], area_km2=b["area_km2"], type=b["type"], occurrence=b["mean_occurrence"])
                 for b in wb["bodies"]}
        layers["water_bodies"] = labels_to_geojson(wb["_label"], g, props)
    R["layers"] = layers
    R["elapsed_s"] = round(time.time() - t0, 1)
    pool.shutdown(wait=False)

    out = _clean(R)
    if not return_rasters:
        prog(100, "Done")
        return out

    # crop the catchment grid to the catchment bounding box (+ margin) to keep files small
    pad = 10
    r0_, r1_ = max(0, rows.min() - pad), min(g.height, rows.max() + pad + 1)
    c0_, c1_ = max(0, cols.min() - pad), min(g.width, cols.max() + pad + 1)
    t = g.transform
    sub_t = [t.a, t.b, t.c + c0_ * t.a, t.d, t.e, t.f + r0_ * t.e]
    crop = (slice(r0_, r1_), slice(c0_, c1_))
    gm = dict(crs=g.crs.to_wkt(), transform=sub_t, height=r1_ - r0_, width=c1_ - c0_, res=g.res)

    def c8(a):
        return None if a is None else a[crop]

    rasters = dict(
        g=gm, catch=catch[crop], dem=dem[crop].astype("float32"), slope=slope[crop].astype("float32"),
        lc=c8(wc), cci_old=c8(cci_old), cci_new=c8(cci_new), io_old=c8(io_old), io_new=c8(io_new),
        soil_loss=None if soil_loss is None else soil_loss[crop], water=c8(occ_g),
        g0=_grid_meta(g0), dem0=dem0.astype("float32"), catch0=catch0, res_level=res_level,
        lc0=wc0, water0=occ0,
    )
    prog(100, "Done")
    return out, rasters
