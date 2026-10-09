# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Land cover composition (ESA WorldCover 2021) and long-term change (ESA CCI 1992 -> 2020)."""
from __future__ import annotations

import numpy as np

WORLDCOVER_NAMES = {
    10: "Tree cover", 20: "Shrubland", 30: "Grassland", 40: "Cropland", 50: "Built-up",
    60: "Bare / sparse", 70: "Snow & ice", 80: "Water", 90: "Wetland", 95: "Mangroves",
    100: "Moss & lichen",
}
WORLDCOVER_COLORS = {
    10: "#006400", 20: "#ffbb22", 30: "#ffff4c", 40: "#f096ff", 50: "#fa0000", 60: "#b4b4b4",
    70: "#f0f0f0", 80: "#0064c8", 90: "#0096a0", 95: "#00cf75", 100: "#fae6a0",
}

CCI_GROUPS = {
    "Forest": [50, 60, 61, 62, 70, 71, 72, 80, 81, 82, 90, 100, 160, 170],
    "Shrubland": [120, 121, 122],
    "Grassland": [110, 130],
    "Cropland": [10, 11, 12, 20, 30, 40],
    "Wetland": [180],
    "Urban": [190],
    "Bare / sparse": [140, 150, 151, 152, 153, 200, 201, 202],
    "Water": [210],
    "Snow & ice": [220],
}


IO_GROUPS = {
    "Forest": [2],
    "Rangeland": [11],
    "Cropland": [5],
    "Wetland": [4],
    "Urban": [7],
    "Bare / sparse": [8],
    "Water": [1],
    "Snow & ice": [9],
}
VEG_GROUPS = ("Forest", "Shrubland", "Grassland", "Rangeland", "Wetland")


def worldcover_fractions(lc, mask):
    vals = lc[mask & (lc > 0)]
    if vals.size == 0:
        return {}
    u, n = np.unique(vals, return_counts=True)
    return {int(k): float(c / vals.size) for k, c in zip(u, n)}


def cci_group_fractions(lc, mask, groups=None):
    groups = groups or CCI_GROUPS
    vals = lc[mask & (lc > 0) & (lc != 10 if groups is IO_GROUPS else True)]
    if vals.size == 0:
        return None
    out = {}
    for g, codes in groups.items():
        out[g] = float(np.isin(vals, codes).sum() / vals.size)
    return out


def change_summary(f_old, f_new, y_old, y_new, source="ESA CCI Land Cover 300 m", detect_pp=0.5):
    if not f_old or not f_new:
        return None
    delta = {g: 100 * (f_new[g] - f_old[g]) for g in f_new}
    veg_change = sum(delta[g] for g in VEG_GROUPS if g in delta)
    degradation = max(0.0, -veg_change) + 2 * max(0.0, delta.get("Urban", 0)) + max(0.0, delta.get("Bare / sparse", 0))
    return dict(
        source=source, detected=any(abs(v) >= detect_pp for v in delta.values()),
        from_year=y_old, to_year=y_new,
        old_pct={g: 100 * v for g, v in f_old.items()},
        new_pct={g: 100 * v for g, v in f_new.items()},
        change_pp=delta,
        vegetation_change_pp=veg_change,
        forest_change_pp=delta.get("Forest", 0.0),
        urban_change_pp=delta.get("Urban", 0.0),
        cropland_change_pp=delta.get("Cropland", 0.0),
        degradation_index=degradation,
    )
