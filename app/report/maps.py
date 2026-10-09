# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Raster storage and rendering of spatial layers (web overlays + report figures)."""
from __future__ import annotations

import io
import json

import numpy as np
from PIL import Image
from rasterio.crs import CRS
from rasterio.transform import Affine
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds

from app.analysis.landcover import CCI_GROUPS, IO_GROUPS, WORLDCOVER_COLORS, WORLDCOVER_NAMES


# ------------------------------------------------------------------ storage
def save_rasters(path, rasters):
    arrays = {k: v for k, v in rasters.items() if isinstance(v, np.ndarray)}
    meta = {k: v for k, v in rasters.items() if isinstance(v, dict)}
    np.savez_compressed(path, __meta__=np.array(json.dumps(meta, default=lambda o: o.item() if hasattr(o, "item") else str(o))), **arrays)


def load_rasters(path):
    z = np.load(path, allow_pickle=False)
    out = {k: z[k] for k in z.files if k != "__meta__"}
    out.update(json.loads(str(z["__meta__"])))
    return out


def grid_of(meta):
    return CRS.from_wkt(meta["crs"]), Affine(*meta["transform"]), (meta["height"], meta["width"])


# ------------------------------------------------------------------ colour helpers
def _hex(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _ramp(stops, n=256):
    """stops: list of hex colours -> (n,3) uint8 linear ramp."""
    cols = np.array([_hex(c) for c in stops], float)
    x = np.linspace(0, 1, len(stops))
    xi = np.linspace(0, 1, n)
    return np.stack([np.interp(xi, x, cols[:, k]) for k in range(3)], 1).astype(np.uint8)


ELEV_RAMP = ["#2f6b3a", "#6f9a4a", "#c9c27a", "#b08a5a", "#8a6a55", "#f2efe9"]
WATER_RAMP = ["#cfe3f7", "#2a78d6", "#0b3d7a"]
SLOPE_CLASSES = [(0, 2, "#f2f0f7", "0–2 % flat"), (2, 5, "#cbc9e2", "2–5 % gentle"),
                 (5, 10, "#9e9ac8", "5–10 % moderate"), (10, 20, "#756bb1", "10–20 % moderately steep"),
                 (20, 35, "#54278f", "20–35 % steep"), (35, 1e9, "#2d0a5c", "> 35 % very steep")]
EROSION_CLASSES = [(0, 1, "#fff5eb", "< 1 very low"), (1, 5, "#fdd0a2", "1–5 low"), (5, 10, "#fdae6b", "5–10 moderate"),
                   (10, 20, "#f16913", "10–20 high"), (20, 50, "#c2410c", "20–50 very high"),
                   (50, 1e12, "#7f2704", "> 50 severe")]
CHANGE_CLASSES = {1: ("#e34948", "Vegetation lost"), 2: ("#1baf7a", "Vegetation gained"),
                  3: ("#4a3aa7", "Became built-up"), 4: ("#eda100", "Became cropland")}


def _hillshade(dem, res, az=315, alt=45):
    d = np.where(np.isfinite(dem), dem, np.nanmean(dem))
    gy, gx = np.gradient(d, res)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    a, z = np.radians(az), np.radians(90 - alt)
    hs = np.cos(z) * np.cos(slope) + np.sin(z) * np.sin(slope) * np.cos(a - aspect)
    return np.clip(hs, 0, 1)


def _classify(arr, classes):
    rgb = np.zeros(arr.shape + (3,), np.uint8)
    for lo, hi, col, _ in classes:
        m = (arr >= lo) & (arr < hi)
        rgb[m] = _hex(col)
    return rgb


def cci_group_codes(cci, groups=CCI_GROUPS):
    out = np.zeros(cci.shape, np.uint8)
    for i, (g, codes) in enumerate(groups.items(), start=1):
        out[np.isin(cci, codes)] = i
    return out


def change_classes(old, new, groups=CCI_GROUPS):
    names = list(groups.keys())
    go, gn = cci_group_codes(old, groups), cci_group_codes(new, groups)
    veg = [names.index(n) + 1 for n in ("Forest", "Shrubland", "Grassland", "Rangeland", "Wetland") if n in names]
    urban = names.index("Urban") + 1
    crop = names.index("Cropland") + 1
    out = np.zeros(old.shape, np.uint8)
    vo, vn = np.isin(go, veg), np.isin(gn, veg)
    out[vo & ~vn] = 1
    out[~vo & vn & (go > 0)] = 2
    out[(gn == urban) & (go != urban)] = 3
    out[(gn == crop) & (go != crop) & (out == 0)] = 4
    return out


# ------------------------------------------------------------------ layer catalogue
LAYERS = {
    "elevation": "Elevation & relief",
    "slope": "Slope",
    "landcover": "Land cover 2021",
    "lcchange10": "Land-cover change 2017→2023 (10 m)",
    "lcchange": "Land-cover change 1992→2020 (300 m)",
    "erosion": "Soil erosion (RUSLE)",
    "water": "Surface-water occurrence",
}


def available_layers(R):
    out = []
    for k, label in LAYERS.items():
        need = {"elevation": "dem", "slope": "slope", "landcover": "lc", "lcchange": "cci_new", "lcchange10": "io_new",
                "erosion": "soil_loss", "water": "water"}[k]
        if need in R and R[need] is not None:
            out.append(dict(key=k, label=label))
    return out


def render_rgba(R, name):
    """Return (rgba uint8 array on the catchment grid, legend dict)."""
    catch = R["catch"].astype(bool)
    res = R["g"]["res"]
    alpha = np.where(catch, 215, 0).astype(np.uint8)
    if name == "elevation":
        dem = R["dem"]
        v = dem[catch]
        lo, hi = float(np.nanpercentile(v, 1)), float(np.nanpercentile(v, 99))
        idx = np.clip(np.nan_to_num((dem - lo) / max(hi - lo, 1)) * 255, 0, 255).astype(int)
        rgb = _ramp(ELEV_RAMP)[idx].astype(float)
        hs = _hillshade(dem, res)[..., None]
        rgb = (rgb * (0.55 + 0.45 * hs)).astype(np.uint8)
        legend = dict(type="gradient", colors=ELEV_RAMP, min=lo, max=hi, quantity="elev")
    elif name == "slope":
        rgb = _classify(R["slope"], SLOPE_CLASSES)
        legend = dict(type="classes", items=[dict(color=c, label=l) for *_, c, l in SLOPE_CLASSES])
    elif name == "landcover":
        lc = R["lc"]
        rgb = np.zeros(lc.shape + (3,), np.uint8)
        present = []
        for code, col in WORLDCOVER_COLORS.items():
            m = lc == code
            if (m & catch).any():
                rgb[m] = _hex(col)
                present.append(dict(color=col, label=WORLDCOVER_NAMES[code]))
        alpha = np.where(catch & (lc > 0), 225, 0).astype(np.uint8)
        legend = dict(type="classes", items=present)
    elif name in ("lcchange", "lcchange10"):
        if name == "lcchange":
            ch = change_classes(R["cci_old"], R["cci_new"])
        else:
            o, n_ = R["io_old"], R["io_new"]
            ch = change_classes(o, n_, IO_GROUPS)
            ch[(o == 10) | (n_ == 10) | (o == 0) | (n_ == 0)] = 0  # clouds / no data
        rgb = np.full(ch.shape + (3,), 235, np.uint8)
        items = []
        for k, (col, lab) in CHANGE_CLASSES.items():
            m = ch == k
            rgb[m] = _hex(col)
            if (m & catch).any():
                items.append(dict(color=col, label=lab))
        items.append(dict(color="#ebebeb", label="No major change"))
        alpha = np.where(catch, np.where(ch > 0, 235, 90), 0).astype(np.uint8)
        legend = dict(type="classes", items=items)
    elif name == "erosion":
        rgb = _classify(np.nan_to_num(R["soil_loss"]), EROSION_CLASSES)
        legend = dict(type="classes", items=[dict(color=c, label=l) for *_, c, l in EROSION_CLASSES],
                      unit="t/ha/yr")
    elif name == "water":
        w = R["water"].astype(float)
        valid = (w > 0) & (w <= 100)
        idx = np.clip(w * 2.55, 0, 255).astype(int)
        rgb = _ramp(WATER_RAMP)[idx]
        alpha = np.where(catch & valid, 230, np.where(catch, 40, 0)).astype(np.uint8)
        legend = dict(type="gradient", colors=WATER_RAMP, min=1, max=100, quantity="pct",
                      note="% of time water was present, 1984–2020")
    else:
        raise KeyError(name)
    return np.dstack([rgb, alpha]), legend


def web_overlay(R, name):
    """Reproject a layer to Web Mercator and return (png bytes, [[s,w],[n,e]], legend)."""
    rgba, legend = render_rgba(R, name)
    crs, tr, (h, w) = grid_of(R["g"])
    left, top = tr.c, tr.f
    right, bottom = left + w * tr.a, top + h * tr.e
    dst_crs = CRS.from_epsg(3857)
    dt, dw, dh = calculate_default_transform(crs, dst_crs, w, h, left, bottom, right, top)
    scale = max(1.0, max(dw, dh) / 1400)
    dw, dh = int(dw / scale), int(dh / scale)
    dt = dt * Affine.scale(scale)
    out = np.zeros((4, dh, dw), np.uint8)
    for b in range(4):
        reproject(rgba[..., b], out[b], src_transform=tr, src_crs=crs, dst_transform=dt,
                  dst_crs=dst_crs, resampling=Resampling.nearest)
    img = Image.fromarray(np.moveaxis(out, 0, -1), "RGBA")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    l, b_, r_, t_ = dt.c, dt.f + dh * dt.e, dt.c + dw * dt.a, dt.f
    w_, s_, e_, n_ = transform_bounds(dst_crs, "EPSG:4326", l, b_, r_, t_)
    return buf.getvalue(), [[s_, w_], [n_, e_]], legend
