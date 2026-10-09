# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Online open-data providers. Every method fails soft (returns None) so the
pipeline can continue and report reduced data confidence."""
from __future__ import annotations

import datetime as dt
import logging
import math

import numpy as np
from rasterio.enums import Resampling

from app import config
from app.data.http import get_json
from app.data.raster_io import Grid, read_to_grid

log = logging.getLogger(__name__)



class TerrainDownloadError(RuntimeError):
    def __init__(self, tiles):
        self.tiles = tiles
        names = ", ".join(t.rsplit("/", 1)[-1].replace(".tif", "") for t in tiles[:3])
        super().__init__(f"Part of the terrain (DEM tile {names}) could not be downloaded. The "
                         "internet connection or the data server timed out. Please run the screening again.")

def _ns(v):
    return ("N" if v >= 0 else "S") + f"{abs(v):02d}"


def _ew(v):
    return ("E" if v >= 0 else "W") + f"{abs(v):03d}"


def dem_tiles(bounds, template):
    w, s, e, n = bounds
    tiles = []
    for lat in range(math.floor(s), math.floor(n) + 1):
        for lon in range(math.floor(w), math.floor(e) + 1):
            tiles.append(template.format(tile=f"{_ns(lat)}_00_{_ew(lon)}"))
    return tiles


def worldcover_tiles(bounds):
    w, s, e, n = bounds
    tiles = []
    for lat in range(math.floor(s / 3) * 3, math.floor(n / 3) * 3 + 1, 3):
        for lon in range(math.floor(w / 3) * 3, math.floor(e / 3) * 3 + 1, 3):
            tiles.append(config.WORLDCOVER_2021.format(tile=f"{_ns(lat)}{_ew(lon)}"))
    return tiles


# ---------------------------------------------------------------- Planetary Computer
def pc_search(collection, bbox, datetime=None, limit=100):
    params = {"collections": collection, "bbox": ",".join(f"{v:.5f}" for v in bbox),
              "limit": limit}
    if datetime:
        params["datetime"] = datetime
    js = get_json(f"{config.PLANETARY_COMPUTER_STAC}/search", params=params)
    return js.get("features", [])


def pc_sign(href, collection):
    """Sign a Planetary Computer blob URL. Prefer the per-href sign endpoint (always
    picks the right storage account); fall back to the collection token."""
    try:
        js = get_json(f"{config.PLANETARY_COMPUTER_SAS}".replace("/token", "/sign"),
                      params={"href": href}, max_age_s=15 * 60, retries=1)
        if js.get("href"):
            return js["href"]
    except Exception as exc:
        log.info("PC sign endpoint failed (%s); using collection token", exc)
    tok = get_json(f"{config.PLANETARY_COMPUTER_SAS}/{collection}", max_age_s=15 * 60)["token"]
    return f"{href}?{tok}"


def jrc_gcs_tiles(bounds):
    """JRC GSW 10x10 degree tiles on Google Cloud (named by west lon / north lat)."""
    w, s, e, n = bounds
    tiles = []
    for lon in range(math.floor(w / 10) * 10, math.floor(e / 10) * 10 + 1, 10):
        for top in range(math.ceil(s / 10) * 10 if s % 10 else int(s) + 10, math.ceil(n / 10) * 10 + 1, 10):
            lo = f"{abs(lon)}{'E' if lon >= 0 else 'W'}"
            la = f"{abs(top)}{'N' if top >= 0 else 'S'}"
            tiles.append(config.JRC_GSW_GCS.format(tile=f"{lo}_{la}"))
    return sorted(set(tiles))


def _pc_asset_urls(collection, asset, bbox, datetime=None, id_contains=None):
    feats = pc_search(collection, bbox, datetime)
    urls = []
    for f in feats:
        if id_contains and id_contains not in f.get("id", ""):
            continue
        a = f.get("assets", {}).get(asset)
        if a:
            urls.append(pc_sign(a["href"], collection))
    return urls


class OnlineProviders:
    """Fetches open global datasets. No accounts or API keys required."""

    name = "online"

    # ------------------------------------------------------------ rasters
    def dem(self, grid: Grid):
        """Copernicus DEM on `grid`. Tiles that fail to download are patched from the
        90 m DEM; if terrain is still missing for a land tile, raise instead of
        returning a DEM with a hole (a hole acts as a fake outlet and ruins catchments)."""
        b = grid.wgs84_bounds(pad_deg=0.01)
        rs90 = Resampling.average if grid.res > 90 else Resampling.bilinear
        if grid.res < 60:
            failed = []
            arr = read_to_grid(dem_tiles(b, config.COP_DEM_30), grid, Resampling.bilinear, failed=failed)
            if arr is not None and not failed:
                return arr, "Copernicus GLO-30 DEM"
            failed90 = []
            arr90 = read_to_grid(dem_tiles(b, config.COP_DEM_90), grid, rs90, failed=failed90)
            if arr is None:
                arr, src = arr90, "Copernicus GLO-90 DEM"
            else:
                src = "Copernicus GLO-30 DEM (part patched from GLO-90)"
                if arr90 is not None:
                    hole = ~np.isfinite(arr) & np.isfinite(arr90)
                    arr[hole] = arr90[hole]
            if failed90 and failed:
                raise TerrainDownloadError(failed)
            return (arr, src) if arr is not None else (None, None)
        failed = []
        arr = read_to_grid(dem_tiles(b, config.COP_DEM_90), grid, rs90, failed=failed)
        if failed:
            raise TerrainDownloadError(failed)
        return (arr, "Copernicus GLO-90 DEM") if arr is not None else (None, None)

    def worldcover(self, grid: Grid):
        b = grid.wgs84_bounds(pad_deg=0.01)
        rs = Resampling.mode if grid.res > 15 else Resampling.nearest
        arr = read_to_grid(worldcover_tiles(b), grid, rs, src_nodata=0, dtype="uint8", fill=0)
        return arr

    def cci_landcover(self, grid: Grid, year: int):
        try:
            b = grid.wgs84_bounds(pad_deg=0.01)
            urls = _pc_asset_urls("esa-cci-lc", "lccs_class", b, f"{year}-06-01/{year}-06-30",
                                  id_contains=f"-{year}-")
            if not urls:
                return None
            rs = Resampling.mode if grid.res > 300 else Resampling.nearest
            return read_to_grid(urls, grid, rs, src_nodata=0, dtype="uint8", fill=0)
        except Exception as exc:
            log.warning("CCI land cover failed: %s", exc)
            return None

    def io_landcover(self, grid: Grid, year: int):
        """Impact Observatory / Esri 10 m annual land use-land cover (2017-2023)."""
        try:
            b = grid.wgs84_bounds(pad_deg=0.01)
            urls = _pc_asset_urls("io-lulc-annual-v02", "data", b, f"{year}-06-01/{year}-06-30",
                                  id_contains=f"-{year}")
            if not urls:
                return None
            rs = Resampling.mode if grid.res > 15 else Resampling.nearest
            return read_to_grid(urls, grid, rs, src_nodata=0, dtype="uint8", fill=0)
        except Exception as exc:
            log.warning("10 m annual land cover failed: %s", exc)
            return None

    def jrc_occurrence(self, grid: Grid):
        b = grid.wgs84_bounds(pad_deg=0.01)
        # 1) Microsoft Planetary Computer copy (cloud-optimised)
        try:
            urls = _pc_asset_urls("jrc-gsw", "occurrence", b)
            if urls:
                arr = read_to_grid(urls, grid, Resampling.nearest, src_nodata=255, dtype="uint8", fill=255)
                if arr is not None:
                    return arr
        except Exception as exc:
            log.warning("JRC via Planetary Computer failed: %s", exc)
        # 2) JRC's own distribution on Google Cloud Storage
        try:
            return read_to_grid(jrc_gcs_tiles(b), grid, Resampling.nearest, src_nodata=255,
                                dtype="uint8", fill=255)
        except Exception as exc:
            log.warning("JRC via Google Cloud failed: %s", exc)
            return None

    def surface_water(self, grid: Grid, worldcover=None, allow_jrc=True):
        """Water occurrence (0-100 %, 255 = no data) and the source used.
        Primary: JRC Global Surface Water. Fallback: ESA WorldCover water class.
        (allow_jrc=False for coarse grids, where reading JRC at full detail is too slow.)"""
        occ = self.jrc_occurrence(grid) if allow_jrc else None
        if occ is not None:
            return occ, "JRC Global Surface Water 1984-2020 (occurrence >= 10%)"
        wc = worldcover if worldcover is not None else self.worldcover(grid)
        if wc is None:
            return None, None
        occ = np.where(wc == 80, 100, np.where(wc == 90, 30, 0)).astype("uint8")
        occ[wc == 0] = 255
        return occ, "ESA WorldCover 2021 water/wetland classes (JRC unavailable)"

    # ------------------------------------------------------------ point APIs
    # Open-Meteo's free tier counts ~1 "call" per 2 weeks of data per location, so one
    # 30-year daily request is ~800 calls (limits: 5,000/hour, 10,000/day). Requests are
    # therefore cached for good (historical data does not change), nearby sites share the
    # same rounded point, and NASA POWER is used when Open-Meteo refuses.
    _om_blocked_until = 0.0
    last_climate_note = None

    def climate(self, points):
        """Daily precipitation & reference ET, averaged over points.
        Primary: ERA5 via Open-Meteo. Fallback: NASA POWER (precip + Hargreaves ET0)."""
        import time as _t
        end_year = dt.date.today().year - 1
        pts = []
        for lon, lat in points:                      # ERA5-Land grid is 0.1° -> share nearby requests
            q = (round(lon, 1), round(lat, 1))
            if q not in pts:
                pts.append(q)
        pts = pts[:3]
        reason = None
        if _t.time() >= OnlineProviders._om_blocked_until:
            params = {
                "latitude": ",".join(f"{p[1]:.1f}" for p in pts),
                "longitude": ",".join(f"{p[0]:.1f}" for p in pts),
                "start_date": f"{config.CLIMATE_START_YEAR}-01-01",
                "end_date": f"{end_year}-12-31",
                "daily": "precipitation_sum,et0_fao_evapotranspiration",
                "timezone": "UTC",
            }
            try:
                js = get_json(config.OPEN_METEO_ARCHIVE, params=params, timeout=120, retries=1,
                              max_age_s=20 * 365 * 86400)
                out = self._parse_open_meteo(js)
                if out:
                    OnlineProviders.last_climate_note = None
                    return out
                reason = "Open-Meteo returned no data"
            except Exception as exc:
                txt = str(exc)
                if "429" in txt or "limit" in txt.lower():
                    reason = "Open-Meteo free daily/hourly request limit reached"
                    OnlineProviders._om_blocked_until = _t.time() + 3600   # don't hammer it
                else:
                    reason = f"Open-Meteo unavailable ({txt[:120]})"
                log.warning("Open-Meteo failed: %s", exc)
        else:
            reason = "Open-Meteo free request limit reached earlier (retrying after an hour)"
        out = self._nasa_power(pts[0][0], pts[0][1], end_year)
        if out:
            out["source"] += f" (used because {reason})"
            OnlineProviders.last_climate_note = None
            return out
        OnlineProviders.last_climate_note = (reason or "rainfall service unavailable") + "; NASA POWER backup also failed"
        return None

    @staticmethod
    def _parse_open_meteo(js):
        items = js if isinstance(js, list) else [js]
        P, E, dates = [], [], None
        for it in items:
            d = it.get("daily", {})
            dates = d.get("time")
            P.append(np.array([np.nan if v is None else v for v in d.get("precipitation_sum", [])], float))
            E.append(np.array([np.nan if v is None else v for v in d.get("et0_fao_evapotranspiration", [])], float))
        if not dates:
            return None
        return {
            "dates": np.array(dates, dtype="datetime64[D]"),
            "precip": np.nanmean(np.vstack(P), axis=0),
            "et0": np.nanmean(np.vstack(E), axis=0),
            "source": "ERA5 reanalysis via Open-Meteo archive API",
            "n_points": len(items),
        }

    def _nasa_power(self, lon, lat, end_year):
        """NASA POWER daily point data: corrected precipitation (PRECTOTCORR) and
        Hargreaves reference ET from daily max/min temperature."""
        try:
            js = get_json(config.NASA_POWER_DAILY, params={
                "parameters": "PRECTOTCORR,T2M_MAX,T2M_MIN", "community": "AG",
                "longitude": f"{lon:.2f}", "latitude": f"{lat:.2f}",
                "start": f"{config.CLIMATE_START_YEAR}0101", "end": f"{end_year}1231", "format": "JSON",
            }, timeout=120, retries=1, max_age_s=20 * 365 * 86400)
            return power_to_climate(js, lat)
        except Exception as exc:
            log.warning("NASA POWER failed: %s", exc)
            return None

    def soil(self, lon, lat):
        """Topsoil/subsoil texture and organic carbon from ISRIC SoilGrids 250 m."""
        for dlon, dlat in [(0, 0), (0.003, 0), (-0.003, 0), (0, 0.003), (0, -0.003)]:
            params = {"lon": round(lon + dlon, 5), "lat": round(lat + dlat, 5),
                      "property": ["clay", "sand", "silt", "soc"],
                      "depth": ["0-5cm", "5-15cm", "15-30cm", "30-60cm"], "value": "mean"}
            try:
                js = get_json(config.SOILGRIDS_QUERY, params=params, timeout=90, retries=1)
            except Exception as exc:
                log.warning("SoilGrids failed: %s", exc)
                return None
            out = {}
            for layer in js.get("properties", {}).get("layers", []):
                f = layer.get("unit_measure", {}).get("d_factor", 10) or 10
                vals = [d["values"].get("mean") for d in layer.get("depths", [])]
                vals = [v / f for v in vals if v is not None]
                if vals:
                    out[layer["name"]] = float(np.mean(vals))
            if {"clay", "sand"} <= out.keys():
                out.setdefault("silt", max(0.0, 100 - out["clay"] - out["sand"]))
                out["soc_pct"] = out.pop("soc", 10.0) / 10.0  # g/kg -> %
                out["source"] = "ISRIC SoilGrids 2.0 (0-60 cm mean)"
                return out
        return None

    def earthquakes(self, lon, lat, radius_km=150):
        params = {"format": "geojson", "latitude": lat, "longitude": lon,
                  "maxradiuskm": radius_km, "minmagnitude": 4.5, "starttime": "1970-01-01",
                  "orderby": "magnitude", "limit": 500}
        try:
            js = get_json(config.USGS_FDSN, params=params)
        except Exception as exc:
            log.warning("USGS earthquakes failed: %s", exc)
            return None
        evs = []
        for f in js.get("features", []):
            lo, la = f["geometry"]["coordinates"][:2]
            evs.append({"mag": f["properties"].get("mag"), "lon": lo, "lat": la,
                        "place": f["properties"].get("place"),
                        "time": f["properties"].get("time")})
        return evs

    def osm(self, lon, lat, bbox, wide_m=10_000, big=False):
        """bbox = (west, south, east, north) of the catchment. Named water features are
        fetched for the catchment box; settlements and protected areas near the site."""
        w, s_, e, n = bbox
        bb = f"{s_:.5f},{w:.5f},{n:.5f},{e:.5f}"
        ww = "river" if big else "river|stream|canal"
        q = f"""
[out:json][timeout:60];
is_in({lat},{lon})->.a;
(
  area.a["boundary"~"^(protected_area|national_park)$"];
  area.a["leisure"="nature_reserve"];
)->.pa;
.pa out tags;
(
  way["natural"="water"]["name"]({bb});
  relation["natural"="water"]["name"]({bb});
  way["landuse"="reservoir"]({bb});
  way["waterway"~"^({ww})$"]["name"]({bb});
  nwr["waterway"~"^(dam|weir)$"]({bb});
  nwr["boundary"~"^(protected_area|national_park)$"](around:{wide_m},{lat},{lon});
  nwr["leisure"="nature_reserve"](around:{wide_m},{lat},{lon});
  node["place"~"^(city|town|village|hamlet)$"](around:{wide_m},{lat},{lon});
);
out tags center 600;
"""
        for ep in config.OVERPASS_ENDPOINTS:
            try:
                js = get_json(ep, method="POST", data={"data": q}, timeout=45, retries=0)
                return js.get("elements", [])
            except Exception as exc:
                log.warning("Overpass %s failed: %s", ep, exc)
        return None


def hargreaves_et0(tmax, tmin, lat_deg, doy):
    """FAO-56 Hargreaves reference evapotranspiration (mm/day)."""
    phi = np.radians(lat_deg)
    dr = 1 + 0.033 * np.cos(2 * np.pi * doy / 365)
    dec = 0.409 * np.sin(2 * np.pi * doy / 365 - 1.39)
    ws = np.arccos(np.clip(-np.tan(phi) * np.tan(dec), -1, 1))
    ra = 24 * 60 / np.pi * 0.0820 * dr * (ws * np.sin(phi) * np.sin(dec) + np.cos(phi) * np.cos(dec) * np.sin(ws))
    tmean = (tmax + tmin) / 2
    return np.maximum(0.0, 0.0023 * 0.408 * ra * (tmean + 17.8) * np.sqrt(np.maximum(tmax - tmin, 0)))


def power_to_climate(js, lat):
    par = js["properties"]["parameter"]
    keys = sorted(par["PRECTOTCORR"].keys())
    if not keys:
        return None

    def arr(name):
        a = np.array([par[name].get(k, -999) for k in keys], float)
        a[a <= -998] = np.nan
        return a
    dates = np.array([f"{k[:4]}-{k[4:6]}-{k[6:]}" for k in keys], dtype="datetime64[D]")
    doy = (dates - dates.astype("datetime64[Y]")).astype(int) + 1
    et0 = hargreaves_et0(arr("T2M_MAX"), arr("T2M_MIN"), lat, doy)
    return {"dates": dates, "precip": arr("PRECTOTCORR"), "et0": et0,
            "source": "NASA POWER daily (MERRA-2 / IMERG precipitation; Hargreaves ET0)", "n_points": 1}
