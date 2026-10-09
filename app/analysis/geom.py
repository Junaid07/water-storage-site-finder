# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Convert raster masks / pixel paths into WGS84 GeoJSON."""
from __future__ import annotations

import numpy as np
from rasterio import features
from shapely.geometry import LineString, MultiLineString, mapping, shape
import shapely
from shapely.ops import unary_union

from app.data.raster_io import Grid


def mask_to_geojson(mask, grid: Grid, simplify_cells=1.0, props=None, max_parts=400):
    if mask is None or not mask.any():
        return None
    polys = [shape(g) for g, v in features.shapes(mask.astype("uint8"), mask=mask,
                                                   transform=grid.transform) if v == 1]
    if not polys:
        return None
    polys.sort(key=lambda p: -p.area)
    geom = unary_union(polys[:max_parts]).simplify(grid.res * simplify_cells, preserve_topology=True)
    geom = _to_wgs84(geom, grid)
    return dict(type="Feature", geometry=mapping(geom), properties=props or {})


def _to_wgs84(geom, grid: Grid):
    tr = grid.to_wgs84()
    return shapely.transform(geom, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))


def pix_to_xy(grid: Grid, rows, cols):
    t = grid.transform
    rows, cols = np.asarray(rows, float), np.asarray(cols, float)
    return t.c + cols * t.a + rows * t.b, t.f + cols * t.d + rows * t.e


def labels_to_geojson(lab, grid: Grid, props_by_id, simplify_cells=0.75):
    feats = []
    for g, v in features.shapes(lab.astype("int32"), mask=lab > 0, transform=grid.transform):
        v = int(v)
        if v not in props_by_id:
            continue
        geom = shape(g).simplify(grid.res * simplify_cells, preserve_topology=True)
        geom = _to_wgs84(geom, grid)
        feats.append(dict(type="Feature", geometry=mapping(geom), properties=props_by_id[v]))
    return dict(type="FeatureCollection", features=feats)


def rc_lines_to_geojson(lines, grid: Grid, props=None):
    if not lines:
        return None
    tr = grid.to_wgs84()
    ls = []
    for line in lines:
        rows = np.array([p[0] for p in line]) + 0.5
        cols = np.array([p[1] for p in line]) + 0.5
        xs, ys = pix_to_xy(grid, rows, cols)
        lon, lat = tr.transform(xs, ys)
        ls.append(LineString(list(zip(lon, lat))).simplify(grid.res / 111_000 * 0.5))
    return dict(type="Feature", geometry=mapping(MultiLineString(ls)), properties=props or {})


def frac_rc_to_lonlat(grid: Grid, rows, cols):
    xs, ys = pix_to_xy(grid, np.asarray(rows) + 0.5, np.asarray(cols) + 0.5)
    lon, lat = grid.to_wgs84().transform(xs, ys)
    return np.asarray(lon), np.asarray(lat)
