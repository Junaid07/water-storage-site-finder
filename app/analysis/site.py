# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Dam-site geometry: valley cross-section, embankment volume and reservoir
elevation-area-capacity curves from the local 30 m DEM."""
from __future__ import annotations

import numpy as np
from scipy import ndimage

from app.analysis.hydro import FlowGrid
from app.data.raster_io import sample_bilinear

MODE_PARAMS = {
    # crest width = a + H/b ; side slopes (H:V) upstream, downstream ; freeboard
    "dam": dict(heights=[4, 6, 8, 10, 12, 15, 18, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100], us=3.0, ds=2.5,
                freeboard=lambda H: max(1.5, 0.1 * H), storage_target=1.0),
    # water harvesting = check dams, ponds, small earthen bunds: capped at 5 m (≈16 ft)
    "harvest": dict(heights=[1, 1.5, 2, 2.5, 3, 4, 5], us=2.5, ds=2.0,
                    freeboard=lambda H: max(0.3, 0.2 * H), storage_target=0.5),
}


def axis_direction(fg: FlowGrid, r, c, n=15):
    up = fg.upstream_main_path(r, c, n)[-1]
    dn = fg.downstream_path(r, c, n)[-1]
    v = np.array([dn[0] - up[0], dn[1] - up[1]], float)
    if np.hypot(*v) < 1e-6:
        from app.analysis.hydro import OFFSETS
        k = fg.dirn[r, c]
        v = OFFSETS[k].astype(float) if k >= 0 else np.array([1.0, 0.0])
    v /= np.hypot(*v)
    perp = np.array([-v[1], v[0]])
    return v, perp


def cross_section(dem, r, c, perp, max_cells=150, step=0.5):
    s = np.arange(-max_cells, max_cells + step, step)
    rows = r + perp[0] * s
    cols = c + perp[1] * s
    h, w = dem.shape
    inside = (rows >= 0) & (rows <= h - 1) & (cols >= 0) & (cols <= w - 1)
    z = np.full(s.shape, np.nan)
    z[inside] = sample_bilinear(dem, rows[inside], cols[inside])
    return s, z, rows, cols


def _abutments(s, z, zc):
    """Return (s_left, s_right) where ground first rises above zc on each side."""
    mid = np.argmin(np.abs(s))
    left = right = None
    for i in range(mid, -1, -1):
        if np.isnan(z[i]):
            break
        if z[i] >= zc:
            left = i
            break
    for i in range(mid, len(s)):
        if np.isnan(z[i]):
            break
        if z[i] >= zc:
            right = i
            break
    return left, right


def analyse_site(dem, fg: FlowGrid, catch_mask, r, c, mode="dam", heights=None):
    p = MODE_PARAMS[mode]
    res = fg.res
    heights = heights or p["heights"]
    v, perp = axis_direction(fg, r, c)
    s, z, rows, cols = cross_section(dem, r, c, perp,
                                     max_cells=int(4000 / res) if mode == "dam" else int(1000 / res))
    win = dem[max(0, r - 1):r + 2, max(0, c - 1):c + 2]
    z0 = float(np.nanmin(win))

    h, w = dem.shape
    catch_edge = catch_mask & ~ndimage.binary_erosion(catch_mask)
    rr, cc = np.indices(dem.shape)
    dist_from_dam = np.hypot(rr - r, cc - c) * res
    struct = np.ones((3, 3), bool)

    curve = []
    for H in heights:
        zc = z0 + H
        fb = p["freeboard"](H)
        fsl = zc - fb
        li, ri = _abutments(s, z, zc)
        closed = li is not None and ri is not None
        lo = li if li is not None else int(np.argmax(~np.isnan(z)))
        hi = ri if ri is not None else len(s) - 1 - int(np.argmax(~np.isnan(z[::-1])))
        seg_z = z[lo:hi + 1]
        d = np.clip(zc - seg_z, 0, None)
        d = np.nan_to_num(d)
        crest_w = 3.0 + H / 5.0
        area_xs = crest_w * d + 0.5 * (p["us"] + p["ds"]) * d ** 2
        fill = float(area_xs.sum() * (s[1] - s[0]) * res)
        crest_len = float((s[hi] - s[lo]) * res)

        # reservoir at full supply level
        wet = catch_mask & (dem <= fsl)
        lab, _ = ndimage.label(wet, structure=struct)
        ids = np.unique(lab[max(0, r - 1):r + 2, max(0, c - 1):c + 2])
        ids = ids[ids > 0]
        res_mask = np.isin(lab, ids) if ids.size else np.zeros_like(wet)
        area = float(res_mask.sum() * res * res)
        vol = float(np.nansum(np.where(res_mask, fsl - dem, 0)) * res * res)
        touches_edge = bool(res_mask[0, :].any() or res_mask[-1, :].any()
                            or res_mask[:, 0].any() or res_mask[:, -1].any())
        spill_zone = res_mask & catch_edge & (dist_from_dam > crest_len / 2 + 3 * res)
        spill = bool(spill_zone.sum() > 2)
        entry = dict(H=H, crest_elev=zc, fsl=fsl, freeboard=fb, closed=closed, spill=spill,
                     window_edge=touches_edge, crest_length_m=crest_len, fill_m3=fill,
                     area_km2=area / 1e6, volume_mcm=vol / 1e6,
                     mean_depth_m=(vol / area) if area > 0 else 0.0,
                     wer=(vol / fill) if fill > 0 else 0.0,
                     _mask=res_mask, _lr=(lo, hi))
        curve.append(entry)
        if not closed or spill or touches_edge:
            break

    feasible = [e for e in curve if e["closed"] and not e["spill"] and e["volume_mcm"] > 0]
    # local slope around site (500 m radius)
    gy, gx = np.gradient(dem, res)
    slope = np.hypot(gx, gy) * 100
    near = dist_from_dam <= 500
    return dict(
        z0=z0, axis_dir=v.tolist(), perp=perp.tolist(),
        profile=dict(s=s, z=z, rows=rows, cols=cols),
        curve=curve, feasible=feasible,
        local_slope_pct=float(np.nanmean(slope[near])),
    )


def choose_height(site, mar_mcm, mode, user_H=None):
    """Pick the design height: user value, else the largest feasible height whose
    storage does not exceed target x mean annual runoff."""
    curve = site["curve"]
    if not curve:
        return None
    if user_H:
        best = min(curve, key=lambda e: abs(e["H"] - user_H))
        return best
    feas = site["feasible"]
    if not feas:
        return curve[0]
    target = MODE_PARAMS[mode]["storage_target"] * (mar_mcm or 0)
    ok = [e for e in feas if e["volume_mcm"] <= target] if target > 0 else []
    if ok:
        # among acceptable heights favour best water-to-embankment ratio near the top
        top = ok[-1]
        best_wer = max(ok, key=lambda e: e["wer"])
        return top if top["wer"] >= 0.7 * best_wer["wer"] else best_wer
    return feas[0]


HARVEST_MAX_KM2 = 25.0     # above this, flood flows overwhelm small harvesting structures
HARVEST_IDEAL_KM2 = (0.05, 5.0)


def suggest_harvest_sites(dem, fg: FlowGrid, r_click, c_click, quick_mm, radius_m=3000, max_sites=6,
                          test_height=3.0):
    """Look for small tributary outlets near the clicked point that suit water harvesting
    (catchment 0.05-5 km2) and rank them by storage efficiency and how well they fill."""
    res = fg.res
    acc = fg.acc_km2
    h, w = acc.shape
    rad = int(radius_m / res)
    r0, r1 = max(1, r_click - rad), min(h - 1, r_click + rad)
    c0, c1 = max(1, c_click - rad), min(w - 1, c_click + rad)
    sub = acc[r0:r1, c0:c1]
    rr, cc = np.indices(sub.shape)
    rr += r0
    cc += c0
    inside = np.hypot(rr - r_click, cc - c_click) <= rad
    lo, hi = HARVEST_IDEAL_KM2
    cand = inside & (sub >= max(lo, 0.1)) & (sub <= hi)
    idx = np.flatnonzero(cand)
    if idx.size == 0:
        return []
    flat = rr.ravel()[idx] * w + cc.ravel()[idx]
    ds = fg.ds[flat]
    a_self = acc.ravel()[flat]
    a_ds = np.where(ds >= 0, acc.ravel()[np.clip(ds, 0, None)], np.inf)
    mouth = a_ds >= 1.6 * a_self            # tributary joins a clearly bigger channel
    flat, a_self = flat[mouth], a_self[mouth]
    order = np.argsort(-a_self)
    chosen = []
    for k in order:
        r, c = divmod(int(flat[k]), w)
        # step two cells upstream from the junction so the structure sits in the tributary
        up = fg.upstream_main_path(r, c, 2)[-1]
        if any(np.hypot(up[0] - q[0], up[1] - q[1]) * res < 400 for q in chosen):
            continue
        chosen.append(up)
        if len(chosen) >= 20:
            break
    out = []
    for r, c in chosen:
        mask = fg.catchment(r, c)
        area = float(mask.sum() * res * res / 1e6)
        if area < 0.05:
            continue
        site = analyse_site(dem, fg, mask, r, c, "harvest", heights=[2.0, test_height, 4.0])
        e = next((x for x in site["curve"] if x["H"] == test_height), None) or (site["curve"][-1] if site["curve"] else None)
        if not e or not e["closed"] or e["volume_mcm"] <= 0:
            continue
        runoff_mcm = quick_mm / 1000 * area  # mm * km2 / 1000 = MCM
        fill_ratio = e["volume_mcm"] / max(runoff_mcm, 1e-9)
        s_wer = float(np.interp(e["wer"], [1, 2, 5, 10, 30], [10, 30, 60, 85, 100]))
        s_fill = float(np.interp(fill_ratio, [0.05, 0.2, 0.5, 1, 2, 4], [50, 80, 100, 85, 50, 20]))
        s_slope = float(np.interp(site["local_slope_pct"], [0.5, 2, 5, 15, 30, 50], [40, 80, 100, 80, 40, 10]))
        score = 0.5 * s_wer + 0.3 * s_fill + 0.2 * s_slope
        out.append(dict(r=r, c=c, catchment_km2=area, H=e["H"], volume_mcm=e["volume_mcm"], area_km2=e["area_km2"],
                        crest_length_m=e["crest_length_m"], fill_m3=e["fill_m3"], wer=e["wer"],
                        runoff_mcm=runoff_mcm, fill_ratio=fill_ratio, slope_pct=site["local_slope_pct"],
                        score=round(score, 1),
                        distance_m=float(np.hypot(r - r_click, c - c_click) * res)))
    out.sort(key=lambda d: -d["score"])
    return out[:max_sites]
