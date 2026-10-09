# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""FastAPI web server for Water Storage Site Finder."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fastapi import Request
from app.config import (AREAS_PER_HOUR, BASE_DIR, CACHE_DIR, CACHE_MAX_GB, MAX_AOI_KM2, MAX_QUEUE, PUBLIC,
                        RESULT_MAX_AGE_DAYS, RUNS_PER_HOUR)
from app.analysis.pipeline import VERSION, AnalysisError, run_analysis

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("damfinder")

app = FastAPI(title="Water Storage Site Finder", version=VERSION,
              description="Open-data screening of dam and water-harvesting sites")
STATIC = BASE_DIR / "app" / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

MAX_WORKERS = int(os.environ.get("DAMFINDER_WORKERS", "2"))
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
RESULT_DIR = CACHE_DIR / "results"
RESULT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- public-hosting safeguards
_hits: dict[str, list[float]] = {}


def _client(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")) or "?"


def _limit(request: Request, kind: str, per_hour: int, label: str):
    """Per-visitor hourly limit and an overall queue limit (only when configured)."""
    if MAX_QUEUE:
        with LOCK:
            busy = sum(1 for j in JOBS.values() if j.get("status") == "running")
        if busy >= MAX_QUEUE:
            raise HTTPException(503, "The server is busy with other analyses right now. Please try again in a few minutes.")
    if not per_hour:
        return
    key, now = f"{kind}:{_client(request)}", time.time()
    with LOCK:
        h = [t for t in _hits.get(key, []) if now - t < 3600]
        if len(h) >= per_hour:
            wait = int((3600 - (now - h[0])) / 60) + 1
            raise HTTPException(429, f"You have reached the limit of {per_hour} new {label} per hour on this public "
                                     f"server. Please try again in about {wait} minutes, or run the tool on your own "
                                     "computer for unlimited use (see the project page).")
        h.append(now)
        _hits[key] = h


def _cleanup_loop():
    """Delete old results so the public server's disk does not fill up (rainfall downloads are kept)."""
    while True:
        try:
            now = time.time()
            files = [p for p in RESULT_DIR.iterdir() if p.is_file()]
            if RESULT_MAX_AGE_DAYS:
                for p in files:
                    if now - p.stat().st_mtime > RESULT_MAX_AGE_DAYS * 86400:
                        p.unlink(missing_ok=True)
            if CACHE_MAX_GB:
                files = sorted((p for p in RESULT_DIR.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
                total = sum(p.stat().st_size for p in files)
                while files and total > CACHE_MAX_GB * 1e9:
                    p = files.pop(0)
                    total -= p.stat().st_size
                    p.unlink(missing_ok=True)
        except Exception as exc:  # pragma: no cover
            log.warning("cache clean-up failed: %s", exc)
        time.sleep(6 * 3600)


if RESULT_MAX_AGE_DAYS or CACHE_MAX_GB:
    threading.Thread(target=_cleanup_loop, daemon=True).start()


class AnalyzeRequest(BaseModel):
    lon: float = Field(..., ge=-180, le=180)
    lat: float = Field(..., ge=-60, le=72)
    mode: str = Field("dam", pattern="^(dam|harvest)$")
    snap_radius_m: float | None = Field(None, ge=30, le=3000)
    name: str | None = Field(None, max_length=80)


def _key(req: AnalyzeRequest):
    d = req.model_dump(exclude={"name"})
    d["lon"], d["lat"] = round(d["lon"], 5), round(d["lat"], 5)
    d["v"] = VERSION
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


def _paths(key):
    if not key.isalnum():
        raise HTTPException(404, "Result not found")
    return RESULT_DIR / f"{key}.json", RESULT_DIR / f"{key}_rasters.npz"


def _no_long_dash(x):
    """Remove long dashes (em/horizontal-bar) from every text in a result, including results
    saved by older versions and text that comes from outside sources."""
    if isinstance(x, str):
        return x.replace(" — ", ", ").replace(" ― ", ", ").replace("—", "-").replace("―", "-")
    if isinstance(x, list):
        return [_no_long_dash(v) for v in x]
    if isinstance(x, dict):
        return {k: _no_long_dash(v) for k, v in x.items()}
    return x


def _with_floods(res):
    """Results saved before the flood table existed: add it from the stored rainfall maxima."""
    if res.get("floods") or not (res.get("climate") or {}).get("annual_max_daily"):
        return res
    try:
        from app.analysis.hydrology import flood_table
        c = res["catchment"]
        res["floods"] = flood_table(res["climate"], c["area_km2"], res["runoff_params"]["curve_number"],
                                    c["longest_flow_path_km"] * 1000, c["flow_path_drop_m"], res["input"]["mode"])
    except Exception as exc:
        log.warning("flood table backfill failed: %s", exc)
    return res


def _complete(jp) -> bool:
    """A cached result is reused only if its key data downloaded (else re-run it)."""
    try:
        st = json.loads(jp.read_text()).get("data_status", {})
        return not str(st.get("Climate", "")).startswith("unavailable")
    except Exception:
        return False


def _run(job_id: str, req: AnalyzeRequest, key: str):
    from app.report.maps import save_rasters

    def progress(pct, msg):
        with LOCK:
            JOBS[job_id].update(progress=pct, message=msg)

    try:
        res, rasters = run_analysis(req.lon, req.lat, mode=req.mode, snap_radius_m=req.snap_radius_m,
                                    progress=progress, return_rasters=True)
        res["id"] = key
        res["input"]["name"] = req.name
        jp, rp = _paths(key)
        save_rasters(rp, rasters)
        jp.write_text(json.dumps(res))
        with LOCK:
            JOBS[job_id].update(status="done", progress=100, result_id=key)
    except AnalysisError as exc:
        with LOCK:
            JOBS[job_id].update(status="error", error=str(exc))
    except Exception as exc:
        log.error("Job %s failed: %s\n%s", job_id, exc, traceback.format_exc())
        with LOCK:
            JOBS[job_id].update(status="error", error=f"Unexpected error: {exc}")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


_geo_lock = threading.Lock()
_geo_last = [0.0]


@app.get("/api/geocode")
def geocode(q: str = Query(..., min_length=2, max_length=200)):
    """Place search via OpenStreetMap Nominatim (max 1 request/s, results cached)."""
    from app.data.http import get_json
    q = " ".join(q.split())
    with _geo_lock:
        wait = 1.05 - (time.time() - _geo_last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            js = get_json("https://nominatim.openstreetmap.org/search",
                          params={"q": q, "format": "jsonv2", "limit": 7, "addressdetails": 0},
                          max_age_s=90 * 86400, retries=1, timeout=15)
        except Exception as exc:
            raise HTTPException(502, f"Place search is unavailable right now ({exc}).")
        finally:
            _geo_last[0] = time.time()
    out = []
    for r in js or []:
        try:
            bb = [float(v) for v in r.get("boundingbox", [])]       # [south, north, west, east]
            out.append(dict(name=r.get("display_name", ""), lat=float(r["lat"]), lon=float(r["lon"]),
                            kind=(r.get("type") or r.get("category") or "").replace("_", " "),
                            bbox=bb if len(bb) == 4 else None))
        except (KeyError, ValueError):
            continue
    return _no_long_dash({"results": out, "source": "© OpenStreetMap contributors (Nominatim)"})


@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION, "jobs": len(JOBS), "public": PUBLIC, "max_aoi_km2": MAX_AOI_KM2,
            "runs_per_hour": RUNS_PER_HOUR, "areas_per_hour": AREAS_PER_HOUR}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest, request: Request):
    key = _key(req)
    job_id = uuid.uuid4().hex[:12]
    jp, rp = _paths(key)
    if jp.exists() and rp.exists() and _complete(jp):
        with LOCK:
            JOBS[job_id] = dict(status="done", progress=100, message="Loaded from cache",
                                result_id=key, created=time.time())
        return {"job_id": job_id}
    _limit(request, "site", RUNS_PER_HOUR, "site screenings")
    with LOCK:
        JOBS[job_id] = dict(status="running", progress=0, message="Queued…", created=time.time())
        for k in [k for k, v in JOBS.items() if time.time() - v["created"] > 6 * 3600]:
            JOBS.pop(k, None)
    executor.submit(_run, job_id, req, key)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "Unknown job")
    return _no_long_dash(dict(j))


@app.get("/api/results/{result_id}")
def result(result_id: str):
    jp, _ = _paths(result_id)
    if not jp.exists():
        raise HTTPException(404, "Result not found")
    return JSONResponse(_no_long_dash(_with_floods(json.loads(jp.read_text()))))


def _rasters(result_id):
    from app.report.maps import load_rasters
    _, rp = _paths(result_id)
    if not rp.exists():
        raise HTTPException(404, "Spatial data not found. Please re-run this site")
    return load_rasters(rp)


@app.get("/api/results/{result_id}/layers")
def layers(result_id: str):
    from app.report.maps import available_layers
    return available_layers(_rasters(result_id))


@app.get("/api/results/{result_id}/layer/{name}.json")
def layer_meta(result_id: str, name: str):
    _layer_files(result_id, name)
    return JSONResponse(json.loads((RESULT_DIR / f"{result_id}_{name}.json").read_text()))


@app.get("/api/results/{result_id}/layer/{name}.png")
def layer_png(result_id: str, name: str):
    _layer_files(result_id, name)
    return FileResponse(RESULT_DIR / f"{result_id}_{name}.png", media_type="image/png")


def _layer_files(result_id, name):
    from app.report.maps import LAYERS, web_overlay
    if name not in LAYERS:
        raise HTTPException(404, "Unknown layer")
    png = RESULT_DIR / f"{result_id}_{name}.png"
    meta = RESULT_DIR / f"{result_id}_{name}.json"
    if png.exists() and meta.exists():
        return
    try:
        data, bounds, legend = web_overlay(_rasters(result_id), name)
    except KeyError:
        raise HTTPException(404, "Layer not available for this site")
    png.write_bytes(data)
    meta.write_text(json.dumps(dict(bounds=bounds, legend=legend)))


@app.get("/api/results/{result_id}/report.pdf")
def report(result_id: str, units: str = Query("metric", pattern="^(metric|imperial)$"),
           h: float | None = Query(None, ge=0, le=200), name: str | None = Query(None, max_length=80)):
    from app.report.pdf import build_report
    jp, _ = _paths(result_id)
    if not jp.exists():
        raise HTTPException(404, "Result not found")
    res = _no_long_dash(_with_floods(json.loads(jp.read_text())))
    if name:
        res["input"]["name"] = name
    pdf = build_report(res, _rasters(result_id), units=units, height=h)
    import re
    slug = re.sub(r"[^A-Za-z0-9]+", "_", res["input"].get("name") or "").strip("_")
    fname = f"WSSF_{slug + '_' if slug else ''}{res['site']['lat']:.4f}_{res['site']['lon']:.4f}_{units}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ====================================================================== area scan
class AreaRequest(BaseModel):
    aoi: dict
    mode: str = Field("dam", pattern="^(dam|harvest)$")


def _run_area(job_id: str, req: AreaRequest, key: str):
    from app.analysis.area import AreaError, scan_area
    from app.analysis.area_full import FULL_SNAP_M, SHORTLIST, refine_full
    from app.report.maps import save_rasters

    def progress(pct, msg):
        with LOCK:
            JOBS[job_id].update(progress=pct, message=msg)

    def key_for(lon, lat):
        return _key(AnalyzeRequest(lon=lon, lat=lat, mode=req.mode, snap_radius_m=FULL_SNAP_M))

    def save(k, res, ras):
        jp, rp = _paths(k)
        save_rasters(rp, ras)
        jp.write_text(json.dumps(res))

    try:
        quick = scan_area(req.aoi, req.mode, progress=lambda p, m: progress(int(p * 0.55), m),
                          max_sites=SHORTLIST)
        res = refine_full(quick, req.mode, key_for, save, progress)
        res["id"] = key
        (RESULT_DIR / f"area_{key}.json").write_text(json.dumps(res))
        with LOCK:
            JOBS[job_id].update(status="done", progress=100, result_id=key)
    except AreaError as exc:
        with LOCK:
            JOBS[job_id].update(status="error", error=str(exc))
    except Exception as exc:
        log.error("Area job %s failed: %s\n%s", job_id, exc, traceback.format_exc())
        with LOCK:
            JOBS[job_id].update(status="error", error=f"Unexpected error: {exc}")


@app.post("/api/aoi/parse")
async def aoi_parse(files: list[UploadFile] = File(...)):
    from app.analysis.aoi_io import AOIError, read_aoi
    from app.analysis.area import aoi_area_km2
    from shapely.geometry import shape
    data = [(f.filename or "", await f.read()) for f in files]
    if sum(len(d) for _, d in data) > 30_000_000:
        raise HTTPException(413, "Files are too large (30 MB max).")
    try:
        geom = read_aoi(data)
    except AOIError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f"Could not read the file: {exc}")
    return {"geometry": geom, "area_km2": aoi_area_km2(shape(geom))}


def _area_complete(p) -> bool:
    if not p.exists():
        return False
    try:
        a = json.loads(p.read_text())
        if not a.get("climate"):
            return False
        return all(_complete(RESULT_DIR / f"{x['result_id']}.json") for x in a.get("sites", []) if x.get("result_id"))
    except Exception:
        return False


@app.post("/api/area_scan")
def area_scan(req: AreaRequest, request: Request):
    key = hashlib.sha1(json.dumps([req.aoi, req.mode, VERSION], sort_keys=True).encode()).hexdigest()[:16]
    job_id = uuid.uuid4().hex[:12]
    if _area_complete(RESULT_DIR / f"area_{key}.json"):
        with LOCK:
            JOBS[job_id] = dict(status="done", progress=100, message="Loaded from cache", result_id=key, created=time.time())
        return {"job_id": job_id}
    _limit(request, "area", AREAS_PER_HOUR, "area searches")
    with LOCK:
        JOBS[job_id] = dict(status="running", progress=0, message="Queued…", created=time.time())
    executor.submit(_run_area, job_id, req, key)
    return {"job_id": job_id}


@app.get("/api/area_results/{result_id}")
def area_result(result_id: str):
    p = RESULT_DIR / f"area_{result_id}.json"
    if not result_id.isalnum() or not p.exists():
        raise HTTPException(404, "Result not found")
    return JSONResponse(_no_long_dash(json.loads(p.read_text())))
