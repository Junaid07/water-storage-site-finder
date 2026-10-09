"""Offline tests: python -m pytest -q"""
import numpy as np

from app.analysis.hydro import FlowGrid
from app.analysis.pipeline import run_analysis
from tests.synthetic import SyntheticProviders


def test_flowgrid_v_valley():
    n = 300
    y, x = np.mgrid[0:n, 0:n]
    dem = 100 + 0.2 * (n - y) + 0.5 * np.abs(x - 150) + np.random.default_rng(0).random((n, n))
    dem[100:130, 140:160] -= 20  # depression
    fg = FlowGrid(dem, 30)
    assert ((fg.dirn < 0) & ~fg.nodata)[1:-1, 1:-1].sum() == 0  # no interior sinks
    r, c = fg.snap(290, 150, 10, min_cells=500)
    m = fg.catchment(r, c)
    assert m.sum() > 0.5 * n * n


def test_pipeline_dam_and_harvest():
    for mode in ("dam", "harvest"):
        r = run_analysis(73.0, 33.7, mode=mode, providers=SyntheticProviders())
        a = r["assessment"]
        assert 0 <= a["score"] <= 100
        assert a["data_confidence"] == 100
        assert r["catchment"]["area_km2"] > 1
        assert r["chosen"] and r["chosen"]["volume_mcm"] > 0
        assert r["water_bodies"] is not None
        assert r["layers"]["catchment"]
        assert len(r["scenarios"]) >= 3 and r["scenarios"][0]["reservoir"]
        for g in r["assessment"]["groups"]:
            assert g["verdict"]
        for f in r["assessment"]["factors"]:
            assert f["plain"]["metric"] and f["plain"]["imperial"]


def test_pipeline_survives_missing_data():
    class Sparse(SyntheticProviders):
        def climate(self, pts): return None
        def soil(self, lon, lat): return None
        def cci_landcover(self, grid, year): return None
        def jrc_occurrence(self, grid): return None
        def osm(self, *a, **k): return None
        def earthquakes(self, *a, **k): return None
    r, ras = run_analysis(73.0, 33.7, providers=Sparse(), return_rasters=True)
    assert r["assessment"]["data_confidence"] < 100
    assert r["data_status"]["Climate"] == "unavailable"
    from app.report.pdf import build_report
    from app.report.maps import available_layers
    assert build_report(r, ras, units="imperial")[:4] == b"%PDF"
    assert available_layers(ras)


def test_layers_and_pdf(tmp_path):
    from app.report.maps import available_layers, load_rasters, save_rasters, web_overlay
    from app.report.pdf import build_report
    r, ras = run_analysis(73.0, 33.7, providers=SyntheticProviders(), return_rasters=True)
    p = tmp_path / "r.npz"
    save_rasters(p, ras)
    R = load_rasters(p)
    for lay in available_layers(R):
        png, bounds, legend = web_overlay(R, lay["key"])
        assert png[:4] == b"\x89PNG" and len(bounds) == 2
    for units in ("metric", "imperial"):
        pdf = build_report(r, R, units=units, height=10)
        assert pdf[:4] == b"%PDF" and len(pdf) > 50_000


def test_area_scan_and_aoi_upload():
    import json
    from app.analysis.area import scan_area
    from app.analysis.aoi_io import read_aoi
    ring = [[72.95, 33.66], [73.06, 33.66], [73.06, 33.78], [72.95, 33.78], [72.95, 33.66]]
    g = read_aoi([("a.geojson", json.dumps({"type": "Polygon", "coordinates": [ring]}).encode())])
    for mode in ("dam", "harvest"):
        r = scan_area(g, mode, providers=SyntheticProviders(73.0, 33.7))
        assert r["sites"], mode
        assert r["sites"][0]["rank"] == 1 and r["sites"][0]["reservoir"]
        assert all(r["sites"][i]["score"] >= r["sites"][i + 1]["score"] for i in range(len(r["sites"]) - 1))


def test_area_full_scores_match_opened_result():
    from app.analysis.area import scan_area
    from app.analysis.area_full import refine_full
    aoi = {"type": "Polygon", "coordinates": [[[72.97, 33.68], [73.03, 33.68], [73.03, 33.74], [72.97, 33.74], [72.97, 33.68]]]}
    P = SyntheticProviders(73.0, 33.7)
    store = {}
    q = scan_area(aoi, "dam", providers=P, max_sites=6)
    r = refine_full(q, "dam", lambda lo, la: f"k{lo:.5f}{la:.5f}", lambda k, res, ras: store.__setitem__(k, res),
                    lambda p, m: None, providers=P)
    assert r["sites"]
    for s in r["sites"]:
        full = store[s["result_id"]]
        assert s["score"] == full["scenarios"][full["default_scenario"]]["assessment"]["score"]
