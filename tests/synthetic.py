"""Offline synthetic data providers for testing the pipeline without internet."""
import numpy as np
from pyproj import Transformer

from app.data.raster_io import Grid


class SyntheticProviders:
    name = "synthetic"

    def __init__(self, lon0=73.0, lat0=33.7, north_extent_m=30_000):
        self.lon0, self.lat0 = lon0, lat0
        self.north_extent = north_extent_m

    def _xy(self, grid: Grid):
        rows, cols = np.indices(grid.shape)
        t = grid.transform
        xs = t.c + (cols + 0.5) * t.a
        ys = t.f + (rows + 0.5) * t.e
        x0, y0 = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True).transform(self.lon0, self.lat0)
        return xs - x0, ys - y0

    def dem(self, grid):
        x, y = self._xy(grid)
        dx = x - 600 * np.sin(y / 4000)
        z = (400 + 0.008 * y
             + 150 * (1 - np.exp(-(dx / 1800) ** 2))
             + 30 * np.sin(x / 2500) * np.sin(y / 3100)
             + 0.004 * np.abs(x))
        z += np.where(y > self.north_extent, 0.05 * (y - self.north_extent), 0)
        z += np.where(y < -15000, 0.0, 0)
        rng = np.random.default_rng(1)
        z += rng.normal(0, 0.5, z.shape)
        return z.astype("float32"), "synthetic DEM"

    def worldcover(self, grid):
        x, y = self._xy(grid)
        lc = np.full(grid.shape, 30, np.uint8)
        lc[(np.sin(x / 1500) + np.cos(y / 1700)) > 0.8] = 10
        lc[(np.abs(x) < 400) & (np.sin(y / 900) > 0.6)] = 40
        lc[(np.hypot(x - 700, y - 1500) < 150)] = 50
        lc[(np.sin(x / 2100) * np.sin(y / 1900)) > 0.85] = 60
        return lc

    def cci_landcover(self, grid, year):
        x, y = self._xy(grid)
        lc = np.full(grid.shape, 130, np.uint8)
        forest = (np.sin(x / 1500) + np.cos(y / 1700)) > (0.4 if year < 2000 else 0.8)
        lc[forest] = 60
        lc[(np.abs(x) < 400)] = 10
        if year >= 2000:
            lc[(np.hypot(x - 700, y - 1500) < 300)] = 190
        return lc

    def io_landcover(self, grid, year):
        x, y = self._xy(grid)
        lc = np.full(grid.shape, 11, np.uint8)
        lc[(np.sin(x / 1500) + np.cos(y / 1700)) > (0.7 if year < 2020 else 0.9)] = 2
        lc[(np.abs(x) < 400)] = 5
        if year >= 2020:
            lc[(np.hypot(x - 700, y - 1500) < 350)] = 7
        return lc

    def jrc_occurrence(self, grid):
        x, y = self._xy(grid)
        occ = np.zeros(grid.shape, np.uint8)
        occ[np.hypot(x + 1200, y - 800) < 250] = 90
        occ[np.hypot(x - 1500, y + 1000) < 120] = 30
        return occ

    def surface_water(self, grid, worldcover=None, allow_jrc=True):
        occ = self.jrc_occurrence(grid)
        return (occ, "synthetic") if occ is not None else (None, None)

    def climate(self, points):
        days = np.arange(np.datetime64("1991-01-01"), np.datetime64("2025-01-01"))
        rng = np.random.default_rng(2)
        doy = (days - days.astype("datetime64[Y]")).astype(int)
        season = 0.5 + 1.5 * np.exp(-((doy - 210) / 35.0) ** 2)
        wet = rng.random(days.size) < 0.12 * season
        p = np.where(wet, rng.gamma(0.8, 14.0, days.size), 0.0)
        et0 = 3.5 + 2.5 * np.sin((doy - 100) / 365 * 2 * np.pi)
        return dict(dates=days, precip=p, et0=et0, source="synthetic", n_points=len(points))

    def soil(self, lon, lat):
        return dict(clay=24.0, sand=38.0, silt=38.0, soc_pct=0.8, source="synthetic")

    def earthquakes(self, lon, lat, radius_km=150):
        return [dict(mag=5.2, lon=lon + 0.4, lat=lat + 0.2, place="synthetic", time=0),
                dict(mag=4.7, lon=lon - 0.3, lat=lat, place="synthetic", time=0)]

    def osm(self, lon, lat, bbox=None, wide_m=10000, big=False):
        return [
            dict(type="way", tags=dict(natural="water", name="Test Lake"), center=dict(lat=lat + 0.007, lon=lon - 0.013)),
            dict(type="way", tags=dict(waterway="river", name="Test River"), center=dict(lat=lat, lon=lon)),
            dict(type="node", tags=dict(place="village", name="Testpur"), lat=lat + 0.0135, lon=lon + 0.0075),
        ]
