# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Climate statistics, water yield (Budyko-Fu), SCS-CN runoff and design flood."""
from __future__ import annotations

import math

import numpy as np

# SCS curve numbers (AMC II) by ESA WorldCover class and hydrologic soil group A..D
CN_TABLE = {
    10: (30, 55, 70, 77),   # tree cover
    20: (35, 56, 70, 77),   # shrubland
    30: (39, 61, 74, 80),   # grassland
    40: (67, 78, 85, 89),   # cropland
    50: (77, 85, 90, 92),   # built-up
    60: (77, 86, 91, 94),   # bare / sparse
    70: (98, 98, 98, 98),   # snow & ice
    80: (98, 98, 98, 98),   # water
    90: (78, 78, 78, 78),   # herbaceous wetland
    95: (70, 70, 70, 70),   # mangroves
    100: (60, 72, 80, 84),  # moss & lichen
}
HSG_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}


def climate_stats(clim):
    dates = clim["dates"]
    P = clim["precip"]
    E = clim["et0"]
    years = dates.astype("datetime64[Y]").astype(int) + 1970
    months = dates.astype("datetime64[M]").astype(int) % 12
    uy = np.unique(years)
    ann_p, ann_e, ann_max, valid_years = [], [], [], []
    for y in uy:
        m = years == y
        if m.sum() < 360 or np.isnan(P[m]).mean() > 0.05:
            continue
        valid_years.append(int(y))
        ann_p.append(np.nansum(P[m]))
        ann_e.append(np.nansum(E[m]))
        ann_max.append(np.nanmax(P[m]))
    ann_p, ann_e, ann_max = map(np.array, (ann_p, ann_e, ann_max))
    n_years = len(ann_p)
    monthly_p = [float(np.nansum(P[months == k]) / max(n_years, 1)) for k in range(12)]
    monthly_e = [float(np.nansum(E[months == k]) / max(n_years, 1)) for k in range(12)]
    mean_p = float(ann_p.mean()) if n_years else float("nan")
    mean_e = float(ann_e.mean()) if n_years else float("nan")
    wet_months = int(sum(1 for p, e in zip(monthly_p, monthly_e) if p > 0.5 * e))
    return dict(
        years=valid_years,
        n_years=n_years,
        annual_precip=ann_p.tolist(), annual_pet=ann_e.tolist(), annual_max_daily=ann_max.tolist(),
        mean_annual_precip_mm=mean_p,
        mean_annual_pet_mm=mean_e,
        aridity_index=mean_p / mean_e if mean_e else float("nan"),
        precip_cv=float(ann_p.std() / ann_p.mean()) if n_years > 2 and ann_p.mean() > 0 else float("nan"),
        monthly_precip_mm=monthly_p, monthly_pet_mm=monthly_e, wet_months=wet_months,
        climate_class=aridity_class(mean_p / mean_e if mean_e else float("nan")),
    )


def aridity_class(ai):
    if not np.isfinite(ai):
        return "unknown"
    if ai < 0.05:
        return "hyper-arid"
    if ai < 0.2:
        return "arid"
    if ai < 0.5:
        return "semi-arid"
    if ai < 0.65:
        return "dry sub-humid"
    return "humid"


def gumbel(series, T):
    x = np.asarray(series, float)
    x = x[np.isfinite(x)]
    if x.size < 5:
        return float("nan")
    kt = -(math.sqrt(6) / math.pi) * (0.5772 + math.log(math.log(T / (T - 1))))
    return float(x.mean() + kt * x.std(ddof=1))


def budyko_fu(P, PET, w=2.6):
    P = np.asarray(P, float)
    PET = np.asarray(PET, float)
    phi = np.where(P > 0, PET / np.maximum(P, 1e-9), np.inf)
    e_ratio = 1 + phi - (1 + phi ** w) ** (1 / w)
    return np.clip(P * (1 - e_ratio), 0, None)


def hydrologic_soil_group(soil):
    if not soil:
        return "C", "default (no soil data)"
    clay, sand = soil.get("clay", 25), soil.get("sand", 40)
    if sand >= 70 and clay < 10:
        g = "A"
    elif clay < 20 and sand >= 45:
        g = "B"
    elif clay < 35:
        g = "C"
    else:
        g = "D"
    return g, f"from texture (clay {clay:.0f}%, sand {sand:.0f}%)"


def composite_cn(lc_fractions, hsg):
    """lc_fractions: dict WorldCover class -> fraction."""
    k = HSG_INDEX[hsg]
    tot = sum(lc_fractions.values())
    if tot <= 0:
        return 75.0
    cn = sum(CN_TABLE.get(cls, CN_TABLE[30])[k] * f for cls, f in lc_fractions.items()) / tot
    return float(cn)


def cn_amc3(cn):
    return 23 * cn / (10 + 0.13 * cn)


def scs_runoff(P, cn, lam=0.2):
    S = 25400.0 / cn - 254.0
    Ia = lam * S
    P = np.asarray(P, float)
    return np.where(P > Ia, (P - Ia) ** 2 / (P - Ia + S), 0.0)


def water_yield(clim, stats, area_km2, cn):
    ann_q = budyko_fu(stats["annual_precip"], stats["annual_pet"])
    mar_mm = float(np.mean(ann_q)) if ann_q.size else float("nan")
    dep_mm = float(np.percentile(ann_q, 25)) if ann_q.size else float("nan")
    daily_q = scs_runoff(np.nan_to_num(clim["precip"]), cn)
    n_years = max(stats["n_years"], 1)
    quick_mm = float(daily_q.sum() / (len(clim["precip"]) / 365.25))
    to_mcm = area_km2 * 1e6 / 1000 / 1e6  # mm -> MCM
    return dict(
        mean_annual_runoff_mm=mar_mm,
        dependable_runoff_mm=dep_mm,
        runoff_coefficient=mar_mm / stats["mean_annual_precip_mm"] if stats["mean_annual_precip_mm"] else float("nan"),
        mean_annual_runoff_mcm=mar_mm * to_mcm,
        dependable_runoff_mcm=dep_mm * to_mcm,
        scs_quickflow_mm=quick_mm,
        scs_quickflow_mcm=quick_mm * to_mcm,
        annual_runoff_series_mm=ann_q.tolist(),
        method="Budyko-Fu (w=2.6) annual water balance; SCS-CN daily quick-flow",
        n_years=n_years,
    )


def design_flood(stats, area_km2, cn, flow_len_m, relief_m, T=100):
    p1 = gumbel(stats["annual_max_daily"], T)
    p24 = 1.13 * p1  # fixed-interval daily -> 24 h correction
    cn3 = cn_amc3(cn)
    q_mm = float(scs_runoff(p24, cn3))
    L = max(flow_len_m, 100.0)
    S = max(relief_m / L, 0.0005)
    tc_h = 0.0195 * L ** 0.77 * S ** -0.385 / 60.0  # Kirpich
    tc_h = max(tc_h, 0.25)
    D = 0.133 * tc_h
    tp = 0.6 * tc_h + D / 2
    qp = 0.208 * area_km2 * q_mm / tp
    return dict(return_period=T, rain_24h_mm=p24, runoff_mm=q_mm, cn_amc3=cn3,
                tc_hours=tc_h, peak_m3s=float(qp),
                note="Indicative only: ERA5 grid rainfall underestimates point extremes.")


FLOOD_T = {"dam": [2, 10, 25, 50, 100, 500, 1000], "harvest": [2, 5, 10, 25, 50, 100]}
DESIGN_T = {"dam": 1000, "harvest": 25}


def flood_table(stats, area_km2, cn, flow_len_m, relief_m, mode="dam"):
    """Peak floods for a range of return periods (same method as design_flood).
    The recommended design return period is marked: 1,000-yr spillway flood for a
    storage dam (small/medium earth dam; high-hazard dams need the PMF), 25-yr for
    harvesting structures (check dams, bunds, ponds)."""
    mode = "harvest" if mode == "harvest" else "dam"
    n = int(np.isfinite(np.asarray(stats["annual_max_daily"], float)).sum())
    rows = []
    for T in FLOOD_T[mode]:
        f = design_flood(stats, area_km2, cn, flow_len_m, relief_m, T=T)
        rows.append(dict(T=T, peak_m3s=f["peak_m3s"], rain_24h_mm=f["rain_24h_mm"], runoff_mm=f["runoff_mm"],
                         extrapolated=T > 3 * max(n, 1), design=T == DESIGN_T[mode]))
    return dict(rows=rows, design_T=DESIGN_T[mode], n_years=n,
        note=("Indicative only. Gumbel fit to the annual maximum daily rainfall of "
              f"{n} years of gridded data; return periods beyond about {3 * n} years are extrapolated "
              "and gridded rainfall understates point extremes, so real floods may be larger."))
