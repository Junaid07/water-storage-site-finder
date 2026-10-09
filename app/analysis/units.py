# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Metric / imperial formatting shared by the plain-language text and the PDF report.
(The web page has an equivalent in static/app.js.)"""
from __future__ import annotations

import math

M_TO_FT = 3.28084
KM_TO_MI = 0.621371
KM2_TO_MI2 = 0.386102
KM2_TO_ACRE = 247.105
MCM_TO_ACFT = 810.713
M3_TO_YD3 = 1.30795
M3_TO_FT3 = 35.3147
CMS_TO_CFS = 35.3147
MM_TO_IN = 0.0393701
T_TO_USTON = 1.10231
THA_TO_TONAC = 0.446090


def _num(v, d=1):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 100:
        return f"{v:,.0f}" if d <= 1 else f"{v:,.1f}"
    return f"{v:,.{d}f}"


class U:
    def __init__(self, system="metric"):
        self.imp = system == "imperial"
        self.system = "imperial" if self.imp else "metric"

    # ---- raw conversions (value, unit)
    def length(self, m, d=0):
        return (_num(m * M_TO_FT, d) + " ft") if self.imp else (_num(m, d) + " m")

    def height(self, m):
        """Structure height: decimals for small (harvesting) structures."""
        return self.length(m, 1 if m < 10 else 0)

    def elev(self, m):
        return self.length(m, 0)

    def dist(self, km, d=1):
        return (_num(km * KM_TO_MI, d) + " mi") if self.imp else (_num(km, d) + " km")

    def area(self, km2):
        if km2 is None:
            return "–"
        if self.imp:
            return _num(km2 * KM2_TO_MI2, 2) + " mi²" if km2 * KM2_TO_MI2 >= 1 else _num(km2 * KM2_TO_ACRE, 0) + " acres"
        return _num(km2, 2) + " km²" if km2 >= 1 else _num(km2 * 100, 1) + " ha"

    def vol(self, mcm):
        if mcm is None:
            return "–"
        if self.imp:
            return _num(mcm * MCM_TO_ACFT, 0) + " acre-ft"
        if mcm < 0.1:
            return _num(mcm * 1e6, 0) + " m³"
        return _num(mcm, 2) + " million m³"

    def fill(self, m3):
        return (_num(m3 * M3_TO_FT3, 0) + " ft³") if self.imp else (_num(m3, 0) + " m³")

    def flow(self, cms):
        return (_num(cms * CMS_TO_CFS, 0) + " cfs") if self.imp else (_num(cms, 0) + " m³/s")

    def depth(self, mm, d=0):
        return (_num(mm * MM_TO_IN, 1) + " in") if self.imp else (_num(mm, d) + " mm")

    def mass(self, t):
        return (_num(t * T_TO_USTON, 0) + " US tons") if self.imp else (_num(t, 0) + " t")

    def rate(self, tha):
        return (_num(tha * THA_TO_TONAC, 2) + " tons/acre/yr") if self.imp else (_num(tha, 2) + " t/ha/yr")

    def fill_word(self, plural=False):
        if self.imp:
            return "cubic feet" if plural else "cubic foot"
        return "cubic metres" if plural else "cubic metre"


def wer_meaning(w):
    """Plain meaning of the water-to-fill ratio (same in any unit system)."""
    if w is None:
        return "–"
    return ("very efficient" if w >= 30 else "efficient" if w >= 10 else
            "average" if w >= 3 else "expensive for the water gained")


WER_GUIDE = "below 3 expensive · 3–10 average · 10–30 efficient · above 30 very efficient"
