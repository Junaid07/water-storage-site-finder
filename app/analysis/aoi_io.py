# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Read an area of interest from uploaded files: zipped or loose shapefile parts,
GeoJSON, or KML. Returns a WGS84 (multi)polygon as GeoJSON."""
from __future__ import annotations

import io
import json
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import mapping, shape
from shapely.ops import unary_union


class AOIError(Exception):
    pass


def _to_wgs84(geom, crs: CRS | None):
    if crs is None:
        x0, y0, x1, y1 = geom.bounds
        if -180 <= x0 <= 180 and -180 <= x1 <= 180 and -90 <= y0 <= 90 and -90 <= y1 <= 90:
            return geom
        raise AOIError("The shapefile has no .prj file and its coordinates are not in degrees. "
                       "please include the .prj (coordinate system) file.")
    if crs.to_epsg() == 4326:
        return geom
    tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    return shapely.transform(geom, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))


def _polys_only(geoms):
    polys = [g.buffer(0) for g in geoms if g is not None and not g.is_empty and g.geom_type in ("Polygon", "MultiPolygon")]
    if not polys:
        raise AOIError("No polygon found. Please provide a polygon (area) layer, not points or lines.")
    return unary_union(polys)


def read_shapefile(parts: dict):
    import shapefile  # pyshp
    shp, shx, dbf = parts.get(".shp"), parts.get(".shx"), parts.get(".dbf")
    if shp is None:
        raise AOIError("The .shp file is missing.")
    r = shapefile.Reader(shp=io.BytesIO(shp), shx=io.BytesIO(shx) if shx else None,
                         dbf=io.BytesIO(dbf) if dbf else None)
    geoms = [shape(s.__geo_interface__) for s in r.shapes()]
    crs = CRS.from_wkt(parts[".prj"].decode("utf-8", "ignore")) if parts.get(".prj") else None
    return _to_wgs84(_polys_only(geoms), crs)


def read_geojson(data: bytes):
    js = json.loads(data.decode("utf-8", "ignore"))
    if js.get("type") == "FeatureCollection":
        geoms = [shape(f["geometry"]) for f in js["features"] if f.get("geometry")]
    elif js.get("type") == "Feature":
        geoms = [shape(js["geometry"])]
    else:
        geoms = [shape(js)]
    return _polys_only(geoms)


def read_kml(data: bytes):
    root = ET.fromstring(data)
    polys = []
    for el in root.iter():
        if el.tag.endswith("Polygon"):
            outer = None
            holes = []
            for b in el.iter():
                if b.tag.endswith("outerBoundaryIs") or b.tag.endswith("innerBoundaryIs"):
                    for c in b.iter():
                        if c.tag.endswith("coordinates") and c.text:
                            pts = [tuple(map(float, t.split(",")[:2])) for t in c.text.split()]
                            if b.tag.endswith("outerBoundaryIs"):
                                outer = pts
                            else:
                                holes.append(pts)
            if outer and len(outer) >= 3:
                polys.append(shape(dict(type="Polygon", coordinates=[outer] + holes)))
    return _polys_only(polys)


def read_aoi(files: list[tuple[str, bytes]]):
    """files: list of (filename, bytes). Returns GeoJSON geometry (WGS84)."""
    parts = {}
    for name, data in files:
        low = name.lower()
        if low.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for n in z.namelist():
                    ext = "." + n.lower().rsplit(".", 1)[-1] if "." in n else ""
                    if ext in (".shp", ".shx", ".dbf", ".prj", ".geojson", ".json", ".kml") and ext not in parts:
                        parts[ext] = z.read(n)
        else:
            ext = "." + low.rsplit(".", 1)[-1] if "." in low else ""
            parts[ext] = data
    if ".shp" in parts:
        geom = read_shapefile(parts)
    elif ".geojson" in parts or ".json" in parts:
        geom = read_geojson(parts.get(".geojson") or parts[".json"])
    elif ".kml" in parts:
        geom = read_kml(parts[".kml"])
    elif ".kmz" in parts:
        with zipfile.ZipFile(io.BytesIO(parts[".kmz"])) as z:
            kml = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
            if not kml:
                raise AOIError("No .kml inside the .kmz file.")
            geom = read_kml(z.read(kml))
    else:
        raise AOIError("Unsupported file. Upload a zipped shapefile (.zip), the .shp/.shx/.dbf/.prj files together, "
                       "a GeoJSON or a KML/KMZ file.")
    return mapping(geom.simplify(0.0002, preserve_topology=True))
