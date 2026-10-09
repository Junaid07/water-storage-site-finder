# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""RUSLE-based gross erosion, sediment delivery and reservoir life."""
from __future__ import annotations

import math

import numpy as np

# Cover-management factor by WorldCover class
C_FACTOR = {10: 0.003, 20: 0.05, 30: 0.04, 40: 0.25, 50: 0.01, 60: 0.45, 70: 0.0,
            80: 0.0, 90: 0.0, 95: 0.003, 100: 0.1}


def r_factor(P):
    """Renard & Freimund (1994), MJ mm ha-1 h-1 yr-1."""
    if not np.isfinite(P):
        return float("nan")
    if P < 850:
        return 0.0483 * P ** 1.61
    return 587.8 - 1.219 * P + 0.004105 * P ** 2


def k_factor(soil):
    """Williams (1995) EPIC K factor, t ha h ha-1 MJ-1 mm-1."""
    if not soil:
        return 0.03
    sa, si, cl = soil.get("sand", 40), soil.get("silt", 35), soil.get("clay", 25)
    oc = soil.get("soc_pct", 1.0)
    sn1 = 1 - sa / 100
    f_csand = 0.2 + 0.3 * math.exp(-0.256 * sa * (1 - si / 100))
    f_clsi = (si / max(cl + si, 1e-6)) ** 0.3
    f_orgc = 1 - 0.25 * oc / (oc + math.exp(3.72 - 2.95 * oc))
    f_hisand = 1 - 0.7 * sn1 / (sn1 + math.exp(-5.51 + 22.9 * sn1))
    return float(0.1317 * f_csand * f_clsi * f_orgc * f_hisand)


def rusle(dem_slope_pct, acc_cells, res, lc, mask, P, soil):
    R = r_factor(P)
    K = k_factor(soil)
    beta = np.arctan(np.nan_to_num(dem_slope_pct) / 100)
    As = np.minimum(acc_cells * res, 300.0)  # contributing slope length capped (sheet/rill)
    LS = (As / 22.13) ** 0.4 * (np.sin(beta) / 0.0896) ** 1.3
    C = np.vectorize(lambda v: C_FACTOR.get(int(v), 0.05), otypes=[float])(lc) if lc is not None \
        else np.full(dem_slope_pct.shape, 0.05)
    A = R * K * LS * C  # t/ha/yr
    A_m = A[mask]
    return dict(_A=A.astype("float32"), R=R, K=K, mean_soil_loss_t_ha_yr=float(np.nanmean(A_m)) if A_m.size else float("nan"),
                mean_LS=float(np.nanmean(LS[mask])), mean_C=float(np.nanmean(C[mask])))


def sediment_yield(mean_loss_t_ha_yr, area_km2):
    gross = mean_loss_t_ha_yr * area_km2 * 100  # t/yr
    a_mi2 = max(area_km2 / 2.59, 0.01)
    sdr = min(1.0, 0.42 * a_mi2 ** -0.125)  # Vanoni (1975)
    return dict(gross_erosion_t_yr=gross, sdr=sdr, sediment_yield_t_yr=gross * sdr,
                specific_yield_t_km2_yr=gross * sdr / max(area_km2, 1e-9))


def reservoir_life(storage_mcm, inflow_mcm, sy_t_yr, bulk_density=1.3):
    if storage_mcm <= 0 or not np.isfinite(sy_t_yr):
        return dict(trap_efficiency=float("nan"), life_50pct_years=float("nan"))
    ci = storage_mcm / max(inflow_mcm, 1e-9)
    te = 0.97 ** (0.19 ** math.log10(max(ci, 1e-6)))  # Brune curve approximation (Dendy 1974)
    vol_yr = sy_t_yr * te / bulk_density / 1e6  # MCM per year
    life = 0.5 * storage_mcm / vol_yr if vol_yr > 0 else float("inf")
    return dict(trap_efficiency=te, sediment_mcm_per_year=vol_yr, life_50pct_years=life,
                capacity_inflow_ratio=ci)
