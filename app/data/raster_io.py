# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Grid definitions and reading remote cloud-optimised GeoTIFFs onto a common grid."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import Affine, from_origin, rowcol, xy
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from pyproj import Transformer

from app.config import GDAL_ENV

log = logging.getLogger(__name__)


def utm_crs(lon: float, lat: float) -> CRS:
    zone = int((lon + 180) // 6) + 1
    zone = min(max(zone, 1), 60)
    epsg = (32600 if lat >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


@dataclass
class Grid:
    crs: CRS
    transform: Affine
    width: int
    height: int
    res: float

    @property
    def shape(self):
        return (self.height, self.width)

    @property
    def bounds(self):
        left, top = self.transform.c, self.transform.f
        return (left, top - self.height * self.res, left + self.width * self.res, top)

    @property
    def cell_area_m2(self) -> float:
        return self.res * self.res

    def wgs84_bounds(self, pad_deg: float = 0.0):
        w, s, e, n = transform_bounds(self.crs, "EPSG:4326", *self.bounds, densify_pts=21)
        return (w - pad_deg, s - pad_deg, e + pad_deg, n + pad_deg)

    def to_wgs84(self):
        return Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)

    def from_wgs84(self):
        return Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)

    def lonlat_to_rc(self, lon, lat):
        x, y = self.from_wgs84().transform(lon, lat)
        r, c = rowcol(self.transform, x, y)
        return int(r), int(c)

    def rc_to_lonlat(self, r, c):
        x, y = xy(self.transform, r, c)
        lon, lat = self.to_wgs84().transform(x, y)
        return float(lon), float(lat)

    def rc_to_xy(self, r, c):
        return xy(self.transform, r, c)


def make_grid(lon: float, lat: float, half_m: float, res: float, crs: CRS | None = None) -> Grid:
    crs = crs or utm_crs(lon, lat)
    x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(lon, lat)
    n = int(round(2 * half_m / res))
    # snap origin to the resolution so different runs share pixel alignment
    left = math.floor((x - half_m) / res) * res
    top = math.ceil((y + half_m) / res) * res
    return Grid(crs, from_origin(left, top, res, res), n, n, float(res))


def _approx_res_m(src) -> float:
    r = abs(src.res[0])
    if src.crs and src.crs.is_geographic:
        return r * 111_320.0
    return r


def _pick_overview(url: str, target_res: float):
    """Return overview_level to open the file at (None = full resolution)."""
    with rasterio.open(url) as src:
        ovs = src.overviews(1)
        native = _approx_res_m(src)
    if not ovs:
        return None
    factor = target_res / max(native, 1e-9)
    level = None
    for i, f in enumerate(ovs):
        if f <= factor / 1.5:
            level = i
    return level


def _is_missing(exc) -> bool:
    s = str(exc)
    return "Not Found" in s or "404" in s or "No such file" in s or "does not exist" in s


def read_to_grid(urls, grid: Grid, resampling=Resampling.bilinear, src_nodata=None,
                 dtype="float32", fill=np.nan, failed=None, retries=3) -> np.ndarray | None:
    """Warp one or more remote rasters onto `grid`. Returns None if nothing readable.
    Tiles that fail for network reasons (not 'missing tile') are retried; tiles that
    still fail are appended to `failed` (if a list is given)."""
    out = np.full(grid.shape, fill, dtype=dtype)
    have = np.zeros(grid.shape, dtype=bool)
    got_any = False
    import time as _t
    with rasterio.Env(**GDAL_ENV):
        for url0 in urls:
          for attempt in range(retries):
            # GDAL remembers a file that failed once for the life of the process; a
            # harmless query string makes it look like a new file on retry.
            url = url0 if attempt == 0 or "?" in url0 else f"{url0}?try={attempt}"
            t0 = _t.time()
            try:
                level = _pick_overview(url, grid.res)
                kwargs = {} if level is None else {"overview_level": level}
                with rasterio.open(url, **kwargs) as src:
                    nod = src_nodata if src_nodata is not None else src.nodata
                    vrt_kwargs = dict(crs=grid.crs, transform=grid.transform,
                                      width=grid.width, height=grid.height,
                                      resampling=resampling)
                    if nod is not None:
                        vrt_kwargs["src_nodata"] = nod
                        vrt_kwargs["nodata"] = nod
                    else:
                        # no nodata in source: use a sentinel so areas outside
                        # this tile are not mistaken for valid zeros
                        vrt_kwargs["nodata"] = -32767.0 if np.dtype(src.dtypes[0]).kind == "f" else 0
                    with WarpedVRT(src, **vrt_kwargs) as vrt:
                        arr = vrt.read(1, masked=True)
                valid = ~np.ma.getmaskarray(arr)
                if arr.dtype.kind == "f":
                    valid &= np.isfinite(arr.data)
                log.info("read %s in %.1fs", url.split("?")[0].rsplit("/", 1)[-1], _t.time() - t0)
                new = valid & ~have
                if new.any():
                    out[new] = arr.data[new]
                    have |= new
                    got_any = True
                break
            except Exception as exc:  # missing tile (e.g. ocean) or network issue
                short = url0.split("?")[0]
                if _is_missing(exc):
                    # missing DEM / WorldCover tiles over the sea are normal
                    log.info("no tile %s: %s", short, exc)
                    break
                log.warning("could not read %s (attempt %d/%d): %s", short, attempt + 1, retries, exc)
                if attempt + 1 < retries:
                    _t.sleep(2 * (attempt + 1))
                elif failed is not None:
                    failed.append(short)
    return out if got_any else None


def sample_bilinear(arr: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Bilinear sample of a 2-D array at fractional pixel centres (row, col)."""
    h, w = arr.shape
    r0 = np.clip(np.floor(rows).astype(int), 0, h - 2)
    c0 = np.clip(np.floor(cols).astype(int), 0, w - 2)
    fr = np.clip(rows - r0, 0, 1)
    fc = np.clip(cols - c0, 0, 1)
    a = arr[r0, c0]
    b = arr[r0, c0 + 1]
    c = arr[r0 + 1, c0]
    d = arr[r0 + 1, c0 + 1]
    return a * (1 - fr) * (1 - fc) + b * (1 - fr) * fc + c * fr * (1 - fc) + d * fr * fc


def make_grid_for_bounds(lonlat_bounds, res: float, buffer_m: float = 0.0, crs: CRS | None = None) -> Grid:
    """Rectangular UTM grid covering a lon/lat bounding box (+ buffer)."""
    w, s, e, n = lonlat_bounds
    crs = crs or utm_crs((w + e) / 2, (s + n) / 2)
    tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    xs, ys = tr.transform([w, e, w, e], [s, s, n, n])
    left = math.floor((min(xs) - buffer_m) / res) * res
    right = math.ceil((max(xs) + buffer_m) / res) * res
    bottom = math.floor((min(ys) - buffer_m) / res) * res
    top = math.ceil((max(ys) + buffer_m) / res) * res
    return Grid(crs, from_origin(left, top, res, res), int((right - left) / res), int((top - bottom) / res), float(res))
