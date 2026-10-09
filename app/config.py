# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""Central configuration for Water Storage Site Finder."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("DAMFINDER_CACHE", BASE_DIR / "cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HTTP_TIMEOUT = 60
_CONTACT = os.environ.get("DAMFINDER_CONTACT", "").strip()
USER_AGENT = ("WaterStorageSiteFinder/1.0 (open-source dam & water-harvesting screening tool"
              + (f"; {_CONTACT}" if _CONTACT else "") + ")")

# Public hosting mode (set DAMFINDER_PUBLIC=1, done automatically in the Dockerfile):
# smaller area searches, per-visitor limits and automatic clean-up of old results.
PUBLIC = os.environ.get("DAMFINDER_PUBLIC", "0") == "1"
MAX_AOI_KM2 = float(os.environ.get("DAMFINDER_MAX_AOI_KM2", "500" if PUBLIC else "3000"))
RUNS_PER_HOUR = int(os.environ.get("DAMFINDER_RUNS_PER_HOUR", "12" if PUBLIC else "0"))      # 0 = unlimited
AREAS_PER_HOUR = int(os.environ.get("DAMFINDER_AREAS_PER_HOUR", "3" if PUBLIC else "0"))
MAX_QUEUE = int(os.environ.get("DAMFINDER_MAX_QUEUE", "12" if PUBLIC else "0"))              # waiting + running jobs
RESULT_MAX_AGE_DAYS = float(os.environ.get("DAMFINDER_RESULT_MAX_AGE_DAYS", "7" if PUBLIC else "0"))
CACHE_MAX_GB = float(os.environ.get("DAMFINDER_CACHE_MAX_GB", "4" if PUBLIC else "0"))

# GDAL settings for fast cloud-optimised GeoTIFF reads over HTTP
GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF,.tiff",
    # NOTE: no GDAL_HTTP_MULTIRANGE=YES: AWS S3 does not support multi-range requests and
    # answers them with the WHOLE file (tens of MB per tile), which made runs extremely slow.
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    GDAL_HTTP_TIMEOUT="60",
    GDAL_HTTP_CONNECTTIMEOUT="15",
    GDAL_HTTP_MAX_RETRY="3",
    GDAL_HTTP_RETRY_DELAY="1",
    VSI_CACHE="TRUE",
    VSI_CACHE_SIZE="100000000",
    GDAL_CACHEMAX=512,
)

# Analysis windows: (half-width in metres, resolution in metres).
# Level 0 is also the "local" grid used for site / reservoir analysis.
CATCHMENT_LEVELS = [
    (12_000, 30),
    (40_000, 90),
    (125_000, 250),
    (400_000, 800),
]

# Climate record used for rainfall / ET statistics
CLIMATE_START_YEAR = 1991

PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
PLANETARY_COMPUTER_SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
NASA_POWER_DAILY = "https://power.larc.nasa.gov/api/temporal/daily/point"
SOILGRIDS_QUERY = "https://rest.isric.org/soilgrids/v2.0/properties/query"
USGS_FDSN = "https://earthquake.usgs.gov/fdsnws/event/1/query"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
COP_DEM_30 = ("https://copernicus-dem-30m.s3.amazonaws.com/"
              "Copernicus_DSM_COG_10_{tile}_00_DEM/Copernicus_DSM_COG_10_{tile}_00_DEM.tif")
COP_DEM_90 = ("https://copernicus-dem-90m.s3.amazonaws.com/"
              "Copernicus_DSM_COG_30_{tile}_00_DEM/Copernicus_DSM_COG_30_{tile}_00_DEM.tif")
JRC_GSW_GCS = ("https://storage.googleapis.com/global-surface-water/downloads2021/"
               "occurrence/occurrence_{tile}v1_4_2021.tif")
WORLDCOVER_2021 = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
                   "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")

# Branding / credit (shown in the app footer, About box and PDF report)
APP_NAME = "Water Storage Site Finder"
AUTHOR = "Junaid Ahmad, PhD"
COPYRIGHT = "© 2026 Junaid Ahmad, PhD"
