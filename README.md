# Water Storage Site Finder

**Open-data screening for storage dams and water harvesting, anywhere in the world.**

Developed by **Junaid Ahmad, PhD** · © 2026 · Licensed under the [GNU AGPL-3.0](LICENSE)

Water Storage Site Finder helps engineers, planners, NGOs and students find and compare places to store water.
Pick a point on a stream, or draw an area, and the tool:

1. **Traces the catchment** that drains to the site from the 30 m Copernicus DEM.
2. **Estimates the water**: 30+ years of daily rainfall and evaporation, average and dry-year inflow, storm runoff,
   and flood peaks from 1-in-2 to 1-in-1,000 years.
3. **Tests every dam height**: crest length, earth fill, storage, lake area and water : fill ratio, with a height slider.
4. **Checks the catchment**: soil erosion and silt, land cover and its change since 1992, existing lakes and dams.
5. **Checks people, environment and safety**: settlements and farmland that would be flooded, protected areas, earthquakes.
6. **Scores the site from 0 to 100**, explained in plain language in four themes, and produces a **PDF report** with maps,
   charts and full references.
7. **Searches a whole area** (drawn on the map or uploaded as a shapefile, GeoJSON or KML) and ranks the 10 best sites,
   each assessed with the full method.

It works in metric or imperial units, has place search and a current-location button, and needs **no accounts or API keys**.

> **Screening only.** Results come from global datasets of about 30 m resolution and simplified methods. They are
> indicative and do not replace topographic survey, geotechnical and geological investigation, hydrological study or
> engineering design.

## Use it

Download it and run it on your own computer (instructions below). It runs in your web browser, has no usage limits,
and handles area searches up to 3,000 km². An online version may follow.

## Run it on your own computer

### Windows (easiest)

1. Install [Python 3.11](https://www.python.org/downloads/) or [Anaconda](https://www.anaconda.com/download).
2. Download this repository (green **Code** button, then **Download ZIP**) and unzip it.
3. Double-click **`setup_windows.bat`** (one time only).
4. Double-click **`run_local.bat`**. Your browser opens at http://localhost:8000.

`check_sources.bat` tests whether each online dataset can be reached from your network.

### Any system (command line)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Command-line screening of one site:

```bash
python analyze.py 33.72 73.05 --mode dam --out result.json
python analyze.py 33.72 73.05 --mode harvest
```

### Docker

```bash
docker build -t wssf .
docker run -p 7860:7860 wssf        # then open http://localhost:7860
```

The Docker image switches on the public-server limits (`DAMFINDER_PUBLIC=1`: 500 km² area searches and per-visitor
hourly limits) for hosting it as a website. Set `-e DAMFINDER_PUBLIC=0` to remove them.

## Data sources

All data are open and are downloaded on demand. Please keep these credits when you use the results.

| Theme | Dataset | Provider and licence |
|---|---|---|
| Terrain | Copernicus GLO-30 / GLO-90 DEM | ESA / Airbus, free licence with attribution |
| Land cover | ESA WorldCover 2021 (10 m) | ESA, CC BY 4.0 |
| Land-cover change | ESA CCI Land Cover 1992 to 2020; Impact Observatory / Esri 10 m annual maps 2017 to 2023 | ESA; Impact Observatory / Esri, CC BY 4.0 |
| Surface water | JRC Global Surface Water 1984 to 2020 | European Commission JRC |
| Rainfall and evaporation | ERA5 via Open-Meteo (backup: NASA POWER) | ECMWF / Copernicus; Open-Meteo, CC BY 4.0, free for non-commercial use; NASA |
| Soil | SoilGrids 2.0 | ISRIC, CC BY 4.0 |
| Earthquakes | ComCat catalogue | USGS, public domain |
| Places, dams, protected areas, place search | OpenStreetMap, Overpass, Nominatim | © OpenStreetMap contributors, ODbL |
| Base maps | OpenTopoMap, OpenStreetMap, Esri World Imagery | as credited on the map |

The free Open-Meteo and Nominatim services are for **non-commercial, light use**. Commercial deployments need the
paid Open-Meteo plan and their own place-search and map-tile providers.

## Methods in brief

* **Catchment:** D8 flow routing on the DEM after depression filling; larger basins use coarser grids automatically.
* **Water balance:** Budyko-Fu (w = 2.6) for long-term runoff; SCS curve number for storm runoff.
* **Floods:** Gumbel fit to annual maximum daily rainfall, SCS unit hydrograph, Kirpich time of concentration.
* **Sediment:** RUSLE, Vanoni delivery ratio, Brune trap efficiency.
* **Dam and reservoir:** valley cross-section, earth embankment volume (crest 3 m + H/5; slopes 3:1 and 2.5:1 for dams,
  2.5:1 and 2:1 for harvesting), storage below full supply level.
* **Score:** weighted factors in four themes, with fatal flags that cap the score. Weights are in
  `app/analysis/scoring.py` and are meant to be tuned.

Full references are listed at the end of every PDF report.

## Project layout

```
app/
  main.py            web server (FastAPI), job queue, results cache, public-server limits
  config.py          settings
  data/              open-data download (DEM, land cover, water, rainfall, soil, OSM, earthquakes)
  analysis/          hydrology, site and reservoir analysis, sediment, land cover, scoring, area search
  report/            map layers and the PDF report
  static/            web page (Leaflet and Chart.js included)
tests/               offline tests with synthetic terrain: python -m pytest -q
```

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `DAMFINDER_PUBLIC` | `0` (`1` in Docker) | Public-server mode with the limits below |
| `DAMFINDER_MAX_AOI_KM2` | 3000 (500 public) | Largest area search |
| `DAMFINDER_RUNS_PER_HOUR` | unlimited (12 public) | New site screenings per visitor per hour |
| `DAMFINDER_AREAS_PER_HOUR` | unlimited (3 public) | New area searches per visitor per hour |
| `DAMFINDER_RESULT_MAX_AGE_DAYS` | keep (7 public) | Delete saved results older than this |
| `DAMFINDER_CACHE` | `./cache` | Where downloads and results are stored |
| `DAMFINDER_CONTACT` | none | Contact e-mail or URL sent to the free data services |

## How to cite

Ahmad, J. (2026). *Water Storage Site Finder* (version 1.6) [software]. Open-data screening for dams and water harvesting.

GitHub also offers a ready citation through the **Cite this repository** button (from `CITATION.cff`).

## Licence

Copyright © 2026 Junaid Ahmad, PhD.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General
Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option)
any later version. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY. See [LICENSE](LICENSE).

If you run a modified version of this tool as a website, the AGPL requires you to offer your modified source code to
its users, and to keep the author's copyright and credit.

Included third-party libraries: [Leaflet](https://leafletjs.com) (BSD-2-Clause) and [Chart.js](https://www.chartjs.org) (MIT).
