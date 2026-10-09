# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Multi-criteria feasibility score (0-100) with plain-language explanations.

Each factor maps a physical indicator to a 0-100 sub-score with a piecewise-linear
curve; factors are combined with weights (re-normalised over factors whose data
are available) and grouped into four easy-to-read themes. Fatal flags cap the
final score.
"""
from __future__ import annotations

import math

import numpy as np

from app.analysis.units import U

GROUPS = [
    ("water", "Water supply", "Is there enough water, reliably, to fill the storage?"),
    ("site", "Site & dam efficiency", "How much water does the site store for the size of dam needed?"),
    ("catchment", "Catchment health", "Will silt, erosion and land degradation shorten the scheme's life?"),
    ("people", "People, environment & safety", "Who and what would be affected, and how risky is the area?"),
]
FACTOR_GROUP = {
    "water_yield": "water", "storage_inflow": "water", "evaporation": "water", "runoff": "water",
    "rainfall": "water",
    "storage_efficiency": "site", "valley_shape": "site", "slope": "site", "soil": "site",
    "catchment_size": "site",
    "sediment": "catchment", "landcover": "catchment", "landcover_change": "catchment",
    "social": "people", "environment": "people", "seismic": "people",
}


def ramp(x, xs, ys):
    if x is None or not np.isfinite(x):
        return None
    return float(np.interp(x, xs, ys))


def verdict(s):
    if s is None:
        return "No data"
    if s >= 80:
        return "Excellent"
    if s >= 60:
        return "Good"
    if s >= 40:
        return "Fair"
    if s >= 20:
        return "Poor"
    return "Very poor"


def _f(key, label, weight, score, value, unit):
    return dict(key=key, label=label, weight=weight, score=score, value=value, unit=unit,
                available=score is not None, group=FACTOR_GROUP.get(key, "site"))


def _evap_loss(r):
    ch, hy = r.get("chosen"), r.get("climate")
    if ch and hy and ch["volume_mcm"] > 0:
        return hy["mean_annual_pet_mm"] / 1000 * ch["area_km2"] * 0.7 / ch["volume_mcm"]
    return None


def factors_dam(r):
    hy, wy, ch = r.get("climate"), r.get("yield"), r.get("chosen")
    sed, lc, lcc = r.get("sediment"), r.get("landcover"), r.get("landcover_change")
    soc, seis, osm, soil = r.get("social"), r.get("seismic"), r.get("osm"), r.get("soil")
    F = []
    s = None
    if wy:
        s = ramp(math.log10(max(wy["mean_annual_runoff_mcm"], 1e-4)), [-2, -1, 0, 1, 2], [10, 40, 70, 90, 100])
        if hy and np.isfinite(hy.get("precip_cv") or np.nan):
            s *= 1 - 0.3 * min(max((hy["precip_cv"] - 0.2) / 0.6, 0), 1)
    F.append(_f("water_yield", "River inflow", 18, s, wy and wy["mean_annual_runoff_mcm"], "MCM/yr"))
    F.append(_f("storage_efficiency", "Water stored per unit of dam", 18,
                ch and ramp(ch["wer"], [1, 3, 10, 30, 100], [5, 25, 60, 85, 100]), ch and ch["wer"], "ratio"))
    vs = None
    if ch:
        vs = 0.0 if not ch["closed"] else ramp(ch["crest_length_m"] / ch["H"], [3, 10, 30, 100, 300], [100, 85, 60, 30, 5])
    F.append(_f("valley_shape", "Valley shape", 9, vs, ch and ch["crest_length_m"] / ch["H"], "m/m"))
    si = None
    if ch and wy and wy["mean_annual_runoff_mcm"] > 0:
        si = ramp(ch["volume_mcm"] / wy["mean_annual_runoff_mcm"], [0.05, 0.2, 0.5, 1.0, 2.0, 4.0], [30, 60, 90, 100, 70, 30])
    F.append(_f("storage_inflow", "Will it fill?", 9, si,
                ch and wy and ch["volume_mcm"] / max(wy["mean_annual_runoff_mcm"], 1e-9), "ratio"))
    F.append(_f("sediment", "Silting up", 10,
                sed and ramp(sed.get("life_50pct_years"), [10, 25, 50, 100, 200], [0, 30, 60, 85, 100]),
                sed and sed.get("life_50pct_years"), "years"))
    loss = _evap_loss(r)
    F.append(_f("evaporation", "Evaporation losses", 5, ramp(loss, [0.05, 0.15, 0.3, 0.6, 1.0], [100, 75, 45, 15, 5]),
                loss, "fraction/yr"))
    F += _common_factors(lc, lcc, soc, osm, weights=(6, 6, 7, 4), recent=r.get("landcover_change_recent"))
    sz = None
    if seis is not None:
        mm = seis.get("max_mag_50km") or 0
        sz = min(ramp(mm, [0, 4.5, 5.5, 6.5, 7.5], [100, 90, 65, 35, 10]),
                 ramp(seis.get("n_m5_100km", 0), [0, 2, 5, 15, 40], [100, 85, 65, 40, 15]))
    F.append(_f("seismic", "Earthquake risk", 4, sz, seis and seis.get("max_mag_50km"), "M"))
    F.append(_f("soil", "Soil for the embankment", 4,
                soil and ramp(soil["clay"], [5, 15, 25, 40, 55], [40, 80, 100, 80, 60]), soil and soil["clay"], "% clay"))
    return F


def factors_harvest(r):
    hy, wy, ch = r.get("climate"), r.get("yield"), r.get("chosen")
    sed, lc, lcc = r.get("sediment"), r.get("landcover"), r.get("landcover_change")
    soc, osm, soil, site = r.get("social"), r.get("osm"), r.get("soil"), r.get("site")
    cat = r.get("catchment") or {}
    F = []
    F.append(_f("runoff", "Storm runoff", 15,
                wy and ramp(wy["scs_quickflow_mm"], [5, 20, 50, 100, 200], [10, 40, 70, 90, 100]),
                wy and wy["scs_quickflow_mm"], "mm/yr"))
    F.append(_f("rainfall", "Rainfall regime", 8,
                hy and ramp(hy["mean_annual_precip_mm"], [100, 200, 400, 800, 1500], [10, 60, 100, 90, 70]),
                hy and hy["mean_annual_precip_mm"], "mm/yr"))
    a = cat.get("area_km2")
    F.append(_f("catchment_size", "Catchment size for harvesting", 10,
                a and ramp(math.log10(max(a, 1e-3)), [-2, math.log10(0.05), math.log10(0.5), math.log10(5), math.log10(25), 2],
                           [40, 90, 100, 80, 35, 10]), a, "km²"))
    F.append(_f("storage_efficiency", "Water stored per unit of bund", 15,
                ch and ramp(ch["wer"], [1, 2, 5, 10, 30], [10, 30, 60, 85, 100]), ch and ch["wer"], "ratio"))
    F.append(_f("slope", "Ground slope", 8,
                site and ramp(site.get("local_slope_pct"), [0.5, 2, 5, 15, 30, 50], [40, 80, 100, 80, 40, 10]),
                site and site.get("local_slope_pct"), "%"))
    F.append(_f("soil", "Soil holds water?", 10,
                soil and ramp(soil["clay"], [5, 15, 25, 40], [20, 60, 90, 100]), soil and soil["clay"], "% clay"))
    si = None
    inflow = wy and wy["scs_quickflow_mcm"]
    if ch and inflow and inflow > 0:
        si = ramp(ch["volume_mcm"] / inflow, [0.05, 0.2, 0.5, 1, 2], [50, 80, 100, 80, 50])
    F.append(_f("storage_inflow", "Will it fill?", 5, si,
                ch and inflow and ch["volume_mcm"] / max(inflow, 1e-9), "ratio"))
    F.append(_f("sediment", "Silting up", 8,
                sed and ramp(sed.get("life_50pct_years"), [5, 10, 20, 40, 80], [0, 30, 60, 85, 100]),
                sed and sed.get("life_50pct_years"), "years"))
    loss = _evap_loss(r)
    F.append(_f("evaporation", "Evaporation losses", 8,
                ramp(loss, [0.1, 0.25, 0.5, 1.0, 2.0], [100, 65, 30, 10, 0]), loss, "fraction/yr"))
    F += _common_factors(lc, lcc, soc, osm, weights=(5, 4, 2, 2), recent=r.get("landcover_change_recent"))
    return F


def _common_factors(lc, lcc, soc, osm, weights, recent=None):
    w_lc, w_lcc, w_soc, w_env = weights
    F = []
    s = veg = None
    if lc:
        veg = sum(lc.get(k, 0) for k in (10, 20, 30, 90, 95, 100)) * 100
        bare = lc.get(60, 0) * 100
        s = max(0.0, ramp(veg, [10, 30, 60, 85], [30, 55, 80, 100]) - 0.5 * max(0.0, bare - 20))
    F.append(_f("landcover", "Catchment vegetation", w_lc, s, veg, "% vegetated"))
    srcs = [c for c in (recent, lcc) if c]
    d = max((c["degradation_index"] for c in srcs), default=None)
    f = _f("landcover_change", "Land degradation (recent & long-term)", w_lcc,
           None if d is None else ramp(d, [0, 2, 5, 10, 20], [100, 85, 65, 40, 10]), d, "index")
    # "no change detected" is not evidence of a healthy catchment -> neutral, never a strength
    f["neutral"] = bool(srcs) and not any(c.get("detected") for c in srcs)
    F.append(f)
    ss = None
    if soc:
        ss = min(ramp(soc["builtup_km2"], [0, 0.01, 0.05, 0.2, 1], [100, 80, 50, 20, 0]),
                 ramp(soc["cropland_km2"], [0, 0.5, 2, 10, 30], [100, 90, 70, 40, 20]))
    F.append(_f("social", "Homes & farmland flooded", w_soc, ss, soc and soc["builtup_km2"], "km²"))
    es = None
    if osm is not None:
        if osm["protected_inside"]:
            es = 0.0
        elif any((p["distance_km"] or 99) < 5 for p in osm["protected_nearby"]):
            es = 60.0
        else:
            es = 100.0
    F.append(_f("environment", "Protected areas", w_env, es, None, ""))
    return F


# ----------------------------------------------------------------- plain language
def explain(key, r, u: U):
    hy, wy, ch = r.get("climate") or {}, r.get("yield") or {}, r.get("chosen") or {}
    sed, soc, osm = r.get("sediment") or {}, r.get("social"), r.get("osm")
    try:
        if key == "water_yield":
            cv = hy.get("precip_cv")
            var = ("flows are fairly steady from year to year" if cv is not None and cv < 0.25 else
                   "flows swing moderately between wet and dry years" if cv is not None and cv < 0.45 else
                   "dry years bring much less water than average")
            return (f"In an average year the river delivers about {u.vol(wy['mean_annual_runoff_mcm'])} "
                    f"(dry-year amount ≈ {u.vol(wy['dependable_runoff_mcm'])}); {var}.")
        if key == "runoff":
            return (f"Storms produce about {u.depth(wy['scs_quickflow_mm'])} of runoff a year, roughly "
                    f"{u.vol(wy['scs_quickflow_mcm'])} from the whole catchment that a structure could catch.")
        if key == "rainfall":
            return (f"Average rainfall is {u.depth(hy['mean_annual_precip_mm'])} a year "
                    f"({hy.get('climate_class', '')} climate). Harvesting pays off most in semi-arid areas.")
        if key == "storage_efficiency":
            w = ch["wer"]
            from app.analysis.units import wer_meaning
            q = wer_meaning(w)
            return (f"Every {u.fill_word()} of earth fill would hold back about {w:.0f} {u.fill_word(True)} of water: "
                    f"{q}. ({u.fill(ch['fill_m3'])} of fill for {u.vol(ch['volume_mcm'])} of storage.)")
        if key == "valley_shape":
            if not ch.get("closed"):
                return "The valley is too wide or too flat for a dam of this height to close it."
            ratio = ch["crest_length_m"] / ch["H"]
            q = "a narrow, gorge-like valley" if ratio < 15 else "a moderately open valley" if ratio < 60 else "a wide, open valley"
            return f"A dam {u.height(ch['H'])} high would need a crest about {u.length(ch['crest_length_m'])} long: {q}."
        if key == "storage_inflow":
            inflow = wy["mean_annual_runoff_mcm"] if r.get("input", {}).get("mode") != "harvest" else wy["scs_quickflow_mcm"]
            x = ch["volume_mcm"] / max(inflow, 1e-9)
            q = ("it would fill easily every year but captures only part of the flow" if x < 0.3 else
                 "a good balance; it should fill in most years" if x <= 1.5 else
                 "it may take more than one wet season to fill and may rarely be full")
            xs = f"{x:.1f}×" if x >= 0.1 else f"only {100 * x:.2g}% of"
            return f"The storage equals {xs} the average yearly inflow; {q}."
        if key == "sediment":
            life = sed.get("life_50pct_years")
            ys = "more than 500 years" if life and life > 500 else f"about {life:.0f} years"
            return (f"Silt washed from the catchment (≈{u.mass(sed.get('sediment_yield_t_yr', 0))} a year) "
                    f"would take {ys} to fill half of the storage.")
        if key == "evaporation":
            x = _evap_loss(r)
            q = ("low" if x < 0.15 else "moderate" if x < 0.3 else "high: a large share of the water is lost"
                 if x < 0.6 else "very high: most stored water would evaporate")
            return f"Evaporation from the water surface would take about {100 * x:.0f}% of the stored volume each year ({q})."
        if key == "slope":
            s = r.get("site", {}).get("local_slope_pct")
            q = "very flat" if s < 1 else "gentle" if s < 5 else "moderate" if s < 15 else "steep"
            return f"The ground around the site slopes about {s:.0f}%: {q}. Gentle-to-moderate slopes suit ponds and check dams."
        if key == "soil":
            c = r["soil"]["clay"]
            if r.get("input", {}).get("mode") == "harvest":
                q = "sandy, so water will seep away quickly" if c < 12 else "moderate, with some seepage" if c < 25 else "clay-rich and holds water well"
            else:
                q = "sandy, so imported clay would be needed for the core" if c < 12 else \
                    "good material for an earth-fill dam" if c <= 40 else "very clayey: usable but may crack when dry"
            return f"Local soil is about {c:.0f}% clay: {q}."
        if key == "landcover":
            lc = r.get("landcover") or {}
            veg = sum(lc.get(k, 0) for k in (10, 20, 30, 90, 95, 100)) * 100
            bare = lc.get(60, 0) * 100
            return (f"{veg:.0f}% of the catchment is covered by trees, shrubs or grass"
                    + (f" and {bare:.0f}% is bare ground" if bare >= 5 else "") + ". More cover means cleaner water and less silt.")
        if key == "landcover_change":
            parts = []
            for c, lab in ((r.get("landcover_change_recent"), "10 m maps"), (r.get("landcover_change"), "300 m maps")):
                if not c:
                    continue
                if c.get("detected"):
                    top = sorted(c["change_pp"].items(), key=lambda kv: -abs(kv[1]))[:3]
                    parts.append(f"{c['from_year']}–{c['to_year']} ({lab}): " +
                                 ", ".join(f"{k.lower()} {v:+.1f}" for k, v in top if abs(v) >= 0.05) +
                                 f" percentage points (net natural vegetation {c['vegetation_change_pp']:+.1f})")
                else:
                    parts.append(f"{c['from_year']}–{c['to_year']} ({lab}): no change large enough to detect")
            txt = "; ".join(parts) + "."
            if all(not (c or {}).get("detected", False) for c in (r.get("landcover_change_recent"), r.get("landcover_change"))):
                txt += " This is treated as neutral, as the maps may simply be too coarse to see local change."
            return txt
        if key == "catchment_size":
            a = r["catchment"]["area_km2"]
            q = ("very small, with little runoff to collect" if a < 0.05 else
                 "ideal for ponds, check dams and bunds" if a <= 5 else
                 "on the large side; structures need strong spillways" if a <= 25 else
                 "too large for water harvesting: flood flows would overtop small structures; consider a small dam "
                 "or one of the side-gully sites suggested")
            return f"The catchment is {u.area(a)}: {q}."
        if key == "social":
            names = (soc or {}).get("settlements_flooded") or []
            t = (f"{u.area(soc['builtup_km2'])} of built-up land and {u.area(soc['cropland_km2'])} of farmland "
                 f"would be under water")
            return t + (f"; mapped villages affected: {', '.join(names)}." if names else ".")
        if key == "environment":
            if osm["protected_inside"]:
                return "The site is inside a protected area: " + ", ".join(osm["protected_inside"]) + "."
            near = [p for p in osm["protected_nearby"] if (p["distance_km"] or 99) < 5]
            if near:
                return f"A protected area ({near[0]['name']}) lies within {u.dist(5, 0)} of the site."
            return "No protected areas are mapped near the site."
        if key == "seismic":
            s = r["seismic"]
            lg = s.get("largest")
            n = s['n_m5_100km']
            return (f"{n} earthquake{'' if n == 1 else 's'} of magnitude 5 or more within {u.dist(100, 0)} since 1970"
                    + (f"; the largest within {u.dist(150, 0)} was M{lg['mag']:.1f}." if lg else "."))
    except Exception:
        pass
    return "Not enough data to explain this factor."


TRIVIAL_STRENGTHS = {"social", "environment", "landcover_change", "seismic"}


def strengths_and_concerns(A, n=3):
    """Meaningful strengths (no 'nothing happened' items) and real concerns."""
    av = [f for f in A["factors"] if f["available"]]
    strong = [f for f in av if f["score"] >= 60 and f["key"] not in TRIVIAL_STRENGTHS
              and not f.get("neutral") and f["weight"] >= 5]
    strong.sort(key=lambda f: -(f["score"] * f["weight"]))
    weak = [f for f in av if f["score"] < 60 and not f.get("neutral")]
    weak.sort(key=lambda f: f["score"] - f["weight"])
    return strong[:n], weak[:n]


def score(result, mode):
    F = factors_dam(result) if mode == "dam" else factors_harvest(result)
    for f in F:
        if f.get("neutral"):
            f["score"] = 80.0  # assumed stable, but not counted as a strength
        f["verdict"] = "Neutral" if f.get("neutral") else verdict(f["score"])
        f["plain"] = {sys: explain(f["key"], result, U(sys)) if f["available"] else
                      "Data for this factor could not be retrieved, so it is left out of the score."
                      for sys in ("metric", "imperial")}
    avail = [f for f in F if f["available"]]
    wsum = sum(f["weight"] for f in avail)
    total = sum(f["weight"] * f["score"] for f in avail) / wsum if wsum else 0.0

    groups = []
    for key, label, question in GROUPS:
        fs = [f for f in avail if f["group"] == key]
        allf = [f for f in F if f["group"] == key]
        if not allf:
            continue
        w = sum(f["weight"] for f in fs)
        gs = sum(f["weight"] * f["score"] for f in fs) / w if w else None
        fs_inf = [f for f in fs if not f.get("neutral")] or fs
        best = max(fs_inf, key=lambda f: f["score"]) if fs_inf else None
        worst = min(fs_inf, key=lambda f: f["score"]) if fs_inf else None
        if gs is None:
            summ = "No data."
        elif worst["score"] >= 60:
            summ = "No concerns here: every factor in this group rates good or excellent."
        else:
            summ = (f"Main concern: {worst['label'].lower()} ({worst['verdict'].lower()})."
                    + (f" Best: {best['label'].lower()} ({best['verdict'].lower()})." if best is not worst else ""))
        groups.append(dict(key=key, label=label, question=question, score=gs, verdict=verdict(gs),
                           weight=sum(f["weight"] for f in allf), summary=summ,
                           factors=[f["key"] for f in allf]))

    flags = []
    cap = 100.0
    cat = result.get("catchment") or {}
    ch = result.get("chosen")
    min_area = 2.0 if mode == "dam" else 0.05
    if cat.get("area_km2", 1e9) < min_area:
        flags.append(dict(level="fatal", text=f"The catchment is very small ({cat['area_km2']:.2f} km²), too little water for this type of scheme."))
        cap = min(cap, 30)
    if ch is None or not ch.get("closed", False):
        flags.append(dict(level="fatal", text="The valley is too wide or flat to be closed at this dam height."))
        cap = min(cap, 30)
    if mode == "harvest" and cat.get("area_km2", 0) > 25:
        flags.append(dict(level="warning", text=f"The catchment ({cat['area_km2']:.0f} km²) is large for water harvesting; "
                                                  "flood flows would overtop small structures. See the suggested side-gully sites, "
                                                  "or assess this point as a storage dam."))
        cap = min(cap, 60)
    osm = result.get("osm")
    if osm and osm["protected_inside"]:
        flags.append(dict(level="fatal", text="The site lies inside a protected area: " + ", ".join(osm["protected_inside"])))
        cap = min(cap, 40)
    soc = result.get("social")
    if soc and soc["builtup_km2"] > 0.2:
        flags.append(dict(level="fatal", text=f"{soc['builtup_km2']:.2f} km² of built-up land would be flooded."))
        cap = min(cap, 40)
    ex = result.get("existing_reservoir")
    if ex and ex.get("at_site"):
        dam = f" (OpenStreetMap: {ex['osm_dam']})" if ex.get("osm_dam") else ""
        flags.append(dict(level="fatal", text=(
            f"There is already a lake or reservoir here ({ex['area_km2'] * 100:.0f} ha of water, "
            f"{ex['distance_m']:.0f} m from the site){dam}, most likely an existing dam. A new structure at this "
            f"point would duplicate it. The existing reservoir drains about {ex['outlet_catchment_km2']:.1f} km² "
            f"at its outlet ({ex['outlet']['lat']:.5f}, {ex['outlet']['lon']:.5f}); screening the reservoir "
            "itself needs a point on its dam or just below it.")))
        cap = min(cap, 25)
    elif soc and soc.get("existing_water_km2") and ch and ch.get("area_km2"):
        frac = soc["existing_water_km2"] / max(ch["area_km2"], 1e-9)
        if frac >= 0.3:
            flags.append(dict(level="warning", text=(
                f"{frac * 100:.0f} % of the new reservoir area is already open water, so there is an existing "
                "lake or reservoir in the basin; the extra storage gained is smaller than shown.")))
            cap = min(cap, 50)
    wb = result.get("water_bodies")
    if not (ex and ex.get("at_site")) and wb and wb.get("site_occurrence") and wb["site_occurrence"] >= 50:
        flags.append(dict(level="info", text="The selected point is in an existing water body, possibly an existing reservoir or lake."))
    cs = (result.get("data_status") or {}).get("Climate", "")
    if cs.startswith("unavailable"):
        why = cs.split(":", 1)[1].strip() if ":" in cs else "the rainfall service did not respond"
        flags.append(dict(level="warning", text=(
            f"Rainfall and evaporation data could not be downloaded ({why}), so the Water supply theme is left "
            "out and the score is incomplete. Run the screening again later (after an hour, or the next day if "
            "the daily limit was reached).")))
    if cat.get("truncated"):
        flags.append(dict(level="warning", text="The catchment is larger than the biggest analysis window, so its area is underestimated."))
    if ch and ch.get("window_edge"):
        flags.append(dict(level="warning", text="The reservoir reaches the edge of the detailed 30 m window; its volume is a lower bound."))
    if ch and ch.get("spill"):
        flags.append(dict(level="warning", text="At this height water would spill over a low saddle in the surrounding ridge."))
    final = min(total, cap)
    conf = wsum / sum(f["weight"] for f in F) if F else 0
    if final >= 75:
        rating = "High potential"
    elif final >= 55:
        rating = "Moderate potential"
    elif final >= 35:
        rating = "Low potential"
    else:
        rating = "Not recommended"
    return dict(score=round(final, 1), raw_score=round(total, 1), cap=cap, rating=rating,
                factors=F, groups=groups, flags=flags, data_confidence=round(conf * 100))
