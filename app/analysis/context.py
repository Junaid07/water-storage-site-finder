# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Water bodies near the site, OSM context (names, settlements, protected areas)
and seismic hazard summary."""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage


def haversine_km(lon1, lat1, lon2, lat2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def water_bodies(occ, grid, lon, lat, region, min_occ=10, perm_occ=75, min_area_km2=None):
    """Label surface-water bodies from water occurrence inside `region` (the catchment mask).
    (lon, lat) is the dam site, used for distances."""
    if occ is None:
        return None
    valid = occ != 255
    r0, c0 = grid.lonlat_to_rc(lon, lat)
    # include the cell ring around the catchment so a lake at the outlet is not cut off
    region = ndimage.binary_dilation(region, iterations=1)
    water = valid & (occ >= min_occ) & region
    if min_area_km2 is None:
        min_area_km2 = max(0.005, 3 * grid.res ** 2 / 1e6)
    lab, n = ndimage.label(water, structure=np.ones((3, 3), bool))
    bodies = []
    cell_km2 = grid.res ** 2 / 1e6
    if n:
        idx = np.arange(1, n + 1)
        counts = ndimage.sum(water, lab, idx)
        mean_occ = ndimage.mean(occ.astype(float), lab, idx)
        perm_frac = ndimage.mean((occ >= perm_occ).astype(float), lab, idx)
        coms = ndimage.center_of_mass(water, lab, idx)
        small = []
        for i, cnt, mo, pf, com in zip(idx, counts, mean_occ, perm_frac, coms):
            a = cnt * cell_km2
            if a < min_area_km2:
                small.append(i)
                continue
            blon, blat = grid.rc_to_lonlat(com[0], com[1])
            bodies.append(dict(id=int(i), area_km2=float(a), mean_occurrence=float(mo),
                               permanent_fraction=float(pf),
                               type="permanent" if pf >= 0.5 else "seasonal",
                               lon=blon, lat=blat,
                               distance_km=haversine_km(lon, lat, blon, blat)))
        if small:
            lab[np.isin(lab, small)] = 0
    bodies.sort(key=lambda b: -b["area_km2"])
    site_occ = int(occ[r0, c0]) if 0 <= r0 < occ.shape[0] and 0 <= c0 < occ.shape[1] else 255
    return dict(
        region_area_km2=float(region.sum() * grid.res ** 2 / 1e6), resolution_m=grid.res,
        count=len(bodies), total_area_km2=float(sum(b["area_km2"] for b in bodies)),
        permanent_area_km2=float(sum(b["area_km2"] * b["permanent_fraction"] for b in bodies)),
        bodies=bodies[:50], site_occurrence=None if site_occ == 255 else site_occ,
        _label=lab, source="JRC Global Surface Water 1984-2020 (occurrence >= 10%)",
    )


def osm_context(elements, lon, lat, inside=None):
    """inside(lon, lat) -> bool filters water features / dams to the catchment."""
    if elements is None:
        return None
    named_water, places, protected_near, protected_inside, dams = [], [], [], [], []
    seen = set()
    for el in elements:
        t = el.get("tags", {})
        name = t.get("name:en") or t.get("name")
        if el.get("type") == "area":
            protected_inside.append(name or t.get("protection_title") or "Unnamed protected area")
            continue
        cen = el.get("center") or ({"lat": el.get("lat"), "lon": el.get("lon")} if "lat" in el else None)
        d = haversine_km(lon, lat, cen["lon"], cen["lat"]) if cen and cen.get("lat") is not None else None
        if t.get("boundary") in ("protected_area", "national_park") or t.get("leisure") == "nature_reserve":
            protected_near.append(dict(name=name or "Unnamed", distance_km=d,
                                       protect_class=t.get("protect_class")))
        elif "place" in t:
            places.append(dict(name=name, type=t["place"], distance_km=d,
                               lon=cen["lon"] if cen else None, lat=cen["lat"] if cen else None,
                               population=t.get("population")))
        elif t.get("waterway") in ("dam", "weir"):
            if inside and cen and not inside(cen["lon"], cen["lat"]):
                continue
            dams.append(dict(name=name, type=t["waterway"], distance_km=d, osm=f"{el.get('type')}/{el.get('id')}",
                             lon=cen["lon"] if cen else None, lat=cen["lat"] if cen else None))
        elif name and (t.get("natural") == "water" or t.get("landuse") == "reservoir"
                       or t.get("waterway")):
            if inside and cen and not inside(cen["lon"], cen["lat"]):
                continue
            key = (name, t.get("waterway") or t.get("water") or "water")
            if key in seen:
                continue
            seen.add(key)
            named_water.append(dict(name=name, kind=t.get("waterway") or t.get("water") or
                                    t.get("landuse") or "water", distance_km=d, osm=f"{el.get('type')}/{el.get('id')}",
                                    lon=cen["lon"] if cen else None, lat=cen["lat"] if cen else None))
    for lst in (named_water, places, protected_near, dams):
        lst.sort(key=lambda x: x["distance_km"] if x["distance_km"] is not None else 1e9)
    return dict(named_water=named_water[:25], places=places[:25],
                protected_inside=sorted(set(protected_inside)),
                protected_nearby=protected_near[:10], existing_dams=dams[:10],
                source="OpenStreetMap via Overpass API")


def seismic_summary(events, lon, lat):
    if events is None:
        return None
    out = dict(n_m45_150km=len(events), n_m5_100km=0, n_m6_100km=0, max_mag_50km=None,
               max_mag_150km=None, largest=None, since=1970,
               source="USGS ComCat earthquake catalogue (M>=4.5 since 1970)")
    for e in events:
        if e["mag"] is None:
            continue
        d = haversine_km(lon, lat, e["lon"], e["lat"])
        if d <= 100 and e["mag"] >= 5:
            out["n_m5_100km"] += 1
        if d <= 100 and e["mag"] >= 6:
            out["n_m6_100km"] += 1
        if d <= 50:
            out["max_mag_50km"] = max(out["max_mag_50km"] or 0, e["mag"])
        out["max_mag_150km"] = max(out["max_mag_150km"] or 0, e["mag"])
        if out["largest"] is None or e["mag"] > out["largest"]["mag"]:
            out["largest"] = dict(mag=e["mag"], place=e["place"], distance_km=d)
    return out
