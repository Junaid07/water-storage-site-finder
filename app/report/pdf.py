# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Junaid Ahmad, PhD
"""PDF screening report (reportlab + matplotlib)."""
from __future__ import annotations

import io
import math
import datetime as dt

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
import numpy as np  # noqa: E402
from pyproj import Transformer  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,  # noqa: E402
                                Spacer, Table, TableStyle)

from app.config import APP_NAME, AUTHOR, COPYRIGHT  # noqa: E402
from app.analysis.units import U, M_TO_FT, MCM_TO_ACFT, KM2_TO_MI2, MM_TO_IN, wer_meaning, WER_GUIDE  # noqa: E402
from app.report.maps import _hillshade, grid_of, render_rgba  # noqa: E402

# Unicode-capable fonts (², ³, ≈, ×, →) shipped with matplotlib
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
_FD = matplotlib.get_data_path() + "/fonts/ttf/"
for _n, _f in [("DejaVu", "DejaVuSans.ttf"), ("DejaVu-Bold", "DejaVuSans-Bold.ttf"),
               ("DejaVu-Oblique", "DejaVuSans-Oblique.ttf"), ("DejaVu-BoldOblique", "DejaVuSans-BoldOblique.ttf")]:
    pdfmetrics.registerFont(TTFont(_n, _FD + _f))
pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold", italic="DejaVu-Oblique",
                              boldItalic="DejaVu-BoldOblique")

INK = "#1c2530"
MUTED = "#5d6b7a"
ACCENT = "#1f6fb2"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"

plt.rcParams.update({"font.size": 8.5, "axes.edgecolor": "#9aa5b1", "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.color": "#e6eaee",
                     "grid.linewidth": 0.6, "font.family": "DejaVu Sans"})

REFERENCES = [
    ("Copernicus DEM", "European Space Agency & Airbus (2022). <i>Copernicus Global Digital Elevation Model (GLO-30 / GLO-90)</i>. "
     "Distributed by AWS Open Data. https://doi.org/10.5270/ESA-c5d3d65"),
    ("ESA WorldCover", "Zanaga, D., Van De Kerchove, R., Daems, D., et al. (2022). <i>ESA WorldCover 10 m 2021 v200</i>. "
     "Zenodo. https://doi.org/10.5281/zenodo.7254221"),
    ("ESA CCI Land Cover", "ESA Land Cover CCI / Copernicus Climate Change Service (2019). <i>Land cover classification gridded maps "
     "from 1992 to present derived from satellite observations</i>. https://doi.org/10.24381/cds.006f2c9a"),
    ("JRC Global Surface Water", "Pekel, J.-F., Cottam, A., Gorelick, N., & Belward, A. S. (2016). High-resolution mapping of global "
     "surface water and its long-term changes. <i>Nature</i>, 540, 418–422. https://doi.org/10.1038/nature20584"),
    ("ERA5", "Hersbach, H., Bell, B., Berrisford, P., et al. (2020). The ERA5 global reanalysis. <i>Quarterly Journal of the "
     "Royal Meteorological Society</i>, 146(730), 1999–2049. https://doi.org/10.1002/qj.3803"),
    ("ERA5-Land", "Muñoz-Sabater, J., Dutra, E., Agustí-Panareda, A., et al. (2021). ERA5-Land: a state-of-the-art global reanalysis "
     "dataset for land applications. <i>Earth System Science Data</i>, 13, 4349–4383. https://doi.org/10.5194/essd-13-4349-2021"),
    ("Open-Meteo", "Zippenfenig, P. (2023). <i>Open-Meteo.com Weather API</i> (historical weather archive). Zenodo. "
     "https://doi.org/10.5281/zenodo.7970649"),
    ("SoilGrids", "Poggio, L., de Sousa, L. M., Batjes, N. H., et al. (2021). SoilGrids 2.0: producing soil information for the globe "
     "with quantified spatial uncertainty. <i>SOIL</i>, 7, 217–240. https://doi.org/10.5194/soil-7-217-2021"),
    ("USGS ComCat", "U.S. Geological Survey, Earthquake Hazards Program (2017). <i>Advanced National Seismic System (ANSS) "
     "Comprehensive Catalog of Earthquake Events and Products</i>. https://doi.org/10.5066/F7MS3QZH"),
    ("OpenStreetMap", "OpenStreetMap contributors (2026). Planet data accessed via the Overpass API. Licensed under the Open "
     "Database License (ODbL). https://www.openstreetmap.org/copyright"),
    ("Planetary Computer", "Microsoft Open Source, McFarland, M., Emanuele, R., Morris, D., & Augspurger, T. (2022). "
     "<i>microsoft/PlanetaryComputer: October 2022</i>. Zenodo. https://doi.org/10.5281/zenodo.7261897"),
    ("D8 flow routing", "O'Callaghan, J. F., & Mark, D. M. (1984). The extraction of drainage networks from digital elevation data. "
     "<i>Computer Vision, Graphics, and Image Processing</i>, 28, 323–344."),
    ("Depression filling", "Soille, P., & Gratin, C. (1994). An efficient algorithm for drainage network extraction on DEMs. "
     "<i>Journal of Visual Communication and Image Representation</i>, 5(2), 181–189."),
    ("Flat routing", "Barnes, R., Lehman, C., & Mulla, D. (2014). An efficient assignment of drainage direction over flat surfaces in "
     "raster digital elevation models. <i>Computers &amp; Geosciences</i>, 62, 128–135."),
    ("Budyko / Fu", "Fu, B. P. (1981). On the calculation of the evaporation from land surface. <i>Scientia Atmospherica Sinica</i>, "
     "5, 23–31; Zhang, L., Hickel, K., Dawes, W. R., et al. (2004). A rational function approach for estimating mean annual "
     "evapotranspiration. <i>Water Resources Research</i>, 40, W02502."),
    ("FAO-56", "Allen, R. G., Pereira, L. S., Raes, D., & Smith, M. (1998). <i>Crop evapotranspiration: Guidelines for computing "
     "crop water requirements</i>. FAO Irrigation and Drainage Paper 56. Rome: FAO."),
    ("SCS Curve Number", "USDA Natural Resources Conservation Service (2004). <i>National Engineering Handbook, Part 630 Hydrology</i>, "
     "Chapters 7, 9, 10 and 16. Washington, DC."),
    ("Kirpich", "Kirpich, Z. P. (1940). Time of concentration of small agricultural watersheds. <i>Civil Engineering</i>, 10(6), 362."),
    ("Gumbel", "Gumbel, E. J. (1958). <i>Statistics of Extremes</i>. New York: Columbia University Press."),
    ("RUSLE", "Renard, K. G., Foster, G. R., Weesies, G. A., McCool, D. K., & Yoder, D. C. (1997). <i>Predicting soil erosion by "
     "water: a guide to conservation planning with the Revised Universal Soil Loss Equation (RUSLE)</i>. USDA Agriculture "
     "Handbook 703."),
    ("R factor", "Renard, K. G., & Freimund, J. R. (1994). Using monthly precipitation data to estimate the R-factor in the revised "
     "USLE. <i>Journal of Hydrology</i>, 157, 287–306."),
    ("K factor", "Williams, J. R. (1995). The EPIC model. In V. P. Singh (Ed.), <i>Computer Models of Watershed Hydrology</i> "
     "(pp. 909–1000). Water Resources Publications."),
    ("LS factor", "Moore, I. D., & Burch, G. J. (1986). Physical basis of the length-slope factor in the Universal Soil Loss "
     "Equation. <i>Soil Science Society of America Journal</i>, 50, 1294–1298."),
    ("Sediment delivery", "Vanoni, V. A. (Ed.) (1975). <i>Sedimentation Engineering</i>. ASCE Manuals and Reports on Engineering "
     "Practice No. 54."),
    ("Trap efficiency", "Brune, G. M. (1953). Trap efficiency of reservoirs. <i>Transactions, American Geophysical Union</i>, 34(3), "
     "407–418; Dendy, F. E. (1974). Sediment trap efficiency of small reservoirs. <i>Transactions of the ASAE</i>, 17(5), 898–901."),
    ("Small dams", "U.S. Bureau of Reclamation (1987). <i>Design of Small Dams</i> (3rd ed.). Denver, CO."),
    ("IO/Esri land cover", "Karra, K., Kontgis, C., Statman-Weil, Z., Mazzariello, J. C., Mathis, M., & Brumby, S. P. (2021). "
     "Global land use / land cover with Sentinel 2 and deep learning. <i>2021 IEEE International Geoscience and Remote Sensing "
     "Symposium (IGARSS)</i>, 4704–4707. https://doi.org/10.1109/IGARSS47720.2021.9553499 (annual v02 maps 2017–2023, "
     "Impact Observatory / Esri)"),
    ("Aridity index", "UNEP (1997). <i>World Atlas of Desertification</i> (2nd ed.). Middleton, N., & Thomas, D. (Eds.). London: Arnold."),
]


# ------------------------------------------------------------------ helpers
def _png(fig, dpi=170):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf


def _img(buf, width_cm, max_h_cm=15.5):
    from reportlab.lib.utils import ImageReader
    ir = ImageReader(buf)
    w, h = ir.getSize()
    buf.seek(0)
    hh = width_cm * h / w
    if hh > max_h_cm:
        width_cm, hh = width_cm * max_h_cm / hh, max_h_cm
    return Image(buf, width=width_cm * cm, height=hh * cm)


def _extent(meta):
    crs, tr, (h, w) = grid_of(meta)
    return crs, tr, (tr.c, tr.c + w * tr.a, tr.f + h * tr.e, tr.f)


def _scalebar(ax, extent, u: U):
    left, right, bottom, top = extent
    span = right - left
    target = span / 5
    if u.imp:
        unit_m, name = 1609.344, "mi"
        if target < 1609:
            unit_m, name = 0.3048, "ft"
    else:
        unit_m, name = 1000.0, "km"
        if target < 1000:
            unit_m, name = 1.0, "m"
    nice = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000]
    val = min(nice, key=lambda v: abs(v * unit_m - target))
    L = val * unit_m
    x0 = left + span * 0.05
    y0 = bottom + (top - bottom) * 0.05
    ax.plot([x0, x0 + L], [y0, y0], color="black", lw=3, solid_capstyle="butt", zorder=10)
    ax.text(x0 + L / 2, y0 + (top - bottom) * 0.02, f"{val:g} {name}", ha="center", va="bottom", fontsize=7.5,
            zorder=10, bbox=dict(fc="white", ec="none", alpha=0.7, pad=1))
    ax.annotate("N", xy=(0.95, 0.93), xytext=(0.95, 0.83), xycoords="axes fraction", ha="center",
                arrowprops=dict(arrowstyle="-|>", color="black"), fontsize=9, fontweight="bold")


def _lines_xy(feature, crs):
    if not feature:
        return []
    tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    g = feature["geometry"]
    parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
    out = []
    for p in parts:
        a = np.array(p)
        x, y = tr.transform(a[:, 0], a[:, 1])
        out.append((x, y))
    return out


def _centers(ext, shape):
    """Pixel-centre coordinate grids for an array whose row 0 is the TOP edge (north-up)."""
    left, right, bottom, top = ext
    h, w = shape
    xs = left + (np.arange(w) + 0.5) * (right - left) / w
    ys = top - (np.arange(h) + 0.5) * (top - bottom) / h
    return np.meshgrid(xs, ys)


def _base_map(ax, R, meta, catch, title=None):
    crs, tr, ext = _extent(meta)
    hs = _hillshade(R["dem"], meta["res"])
    ax.imshow(hs, cmap="gray", extent=ext, vmin=0, vmax=1.15, interpolation="bilinear")
    X, Y = _centers(ext, catch.shape)
    ax.contour(X, Y, catch.astype(float), levels=[0.5], colors=["#e8792b"], linewidths=1.4)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(True)
        s.set_color("#9aa5b1")
    if title:
        ax.set_title(title, fontsize=9.5, loc="left", color=INK)
    return crs, ext


def fig_catchment(R, res, u):
    meta = R["g"]
    catch = R["catch"].astype(bool)
    fig, ax = plt.subplots(figsize=(6.6, 6.6 * meta["height"] / max(meta["width"], 1) if meta["width"] else 6))
    fig.set_size_inches(6.6, min(8.2, max(3.5, 6.6 * meta["height"] / meta["width"])))
    crs, ext = _base_map(ax, R, meta, catch)
    if "water" in R and R["water"] is not None:
        w = R["water"]
        m = (w >= 10) & (w <= 100) & catch
        ov = np.zeros(w.shape + (4,))
        ov[m] = (0.11, 0.30, 0.85, 0.85)
        ax.imshow(ov, extent=ext, interpolation="nearest")
    for x, y in _lines_xy(res["layers"].get("streams"), crs):
        ax.plot(x, y, color=BLUE, lw=0.8)
    tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    sx, sy = tr.transform(res["site"]["lon"], res["site"]["lat"])
    ax.plot(sx, sy, marker="*", ms=14, color="#d62828", mec="white", mew=1.2, zorder=12)
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    _scalebar(ax, ext, u)
    ax.legend(handles=[Patch(fc="none", ec="#e8792b", lw=1.4, label="Catchment boundary"),
                       Patch(color=BLUE, label="Main streams"),
                       Patch(color="#1c4dd9", label="Surface water"),
                       plt.Line2D([], [], marker="*", color="#d62828", ls="", ms=10, label="Dam / structure site")],
              loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=7, frameon=False)
    return _png(fig)


def fig_layer(R, name, u):
    meta = R["g"]
    catch = R["catch"].astype(bool)
    rgba, legend = render_rgba(R, name)
    fig, ax = plt.subplots()
    fig.set_size_inches(6.6, min(7.5, max(3.5, 6.6 * meta["height"] / meta["width"])))
    crs, ext = _base_map(ax, R, meta, catch)
    ax.imshow(rgba, extent=ext, interpolation="nearest")
    _scalebar(ax, ext, u)
    if legend["type"] == "classes":
        handles = [Patch(color=i["color"], label=i["label"]) for i in legend["items"]]
        ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=7, frameon=False,
                  title=legend.get("unit"), title_fontsize=7)
    else:
        lo, hi = legend["min"], legend["max"]
        if legend.get("quantity") == "elev":
            lo, hi, unit = (lo * M_TO_FT, hi * M_TO_FT, "ft") if u.imp else (lo, hi, "m")
        else:
            unit = "%"
        from matplotlib.colors import LinearSegmentedColormap
        cmap = LinearSegmentedColormap.from_list("x", legend["colors"])
        sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(lo, hi))
        cb = fig.colorbar(sm, ax=ax, fraction=0.035, pad=0.02)
        cb.set_label(unit, fontsize=7.5)
        cb.ax.tick_params(labelsize=7)
    return _png(fig)


def fig_site(R, res, scen, u):
    meta = R["g0"]
    crs, tr, (h, w) = grid_of(meta)
    lvl = R["res_level"]
    idx = res["scenarios"].index(scen) if scen in res["scenarios"] else 0
    resm = lvl <= idx
    if not resm.any():
        return None
    rows, cols = np.where(resm)
    pad = max(15, int(0.25 * max(np.ptp(rows), np.ptp(cols))))
    r0, r1 = max(0, rows.min() - pad), min(h, rows.max() + pad)
    c0, c1 = max(0, cols.min() - pad), min(w, cols.max() + pad)
    dem = R["dem0"][r0:r1, c0:c1]
    sub = resm[r0:r1, c0:c1]
    left, top = tr.c + c0 * tr.a, tr.f + r0 * tr.e
    ext = (left, left + (c1 - c0) * tr.a, top + (r1 - r0) * tr.e, top)
    fig, ax = plt.subplots()
    fig.set_size_inches(6.6, min(7.0, max(3.5, 6.6 * (r1 - r0) / (c1 - c0))))
    ax.imshow(_hillshade(dem, meta["res"]), cmap="gray", extent=ext, vmin=0, vmax=1.15)
    zmin, zmax = np.nanmin(dem), np.nanmax(dem)
    step = max(5, round((zmax - zmin) / 15 / 5) * 5)
    X, Y = _centers(ext, dem.shape)
    ax.contour(X, Y, np.nan_to_num(dem, nan=zmin), levels=np.arange(math.floor(zmin / step) * step, zmax, step),
               colors="#7a5c3a", linewidths=0.35, alpha=0.7)
    ov = np.zeros(sub.shape + (4,))
    ov[sub] = (0.21, 0.78, 0.96, 0.75)
    ax.imshow(ov, extent=ext, interpolation="nearest")
    trw = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    a = np.array(scen["dam_axis"]["geometry"]["coordinates"])
    x, y = trw.transform(a[:, 0], a[:, 1])
    ax.plot(x, y, color="#d62828", lw=3.2, solid_capstyle="round")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)
    _scalebar(ax, ext, u)
    ax.legend(handles=[Patch(color="#36c6f4", label=f"Reservoir at full supply"),
                       plt.Line2D([], [], color="#d62828", lw=3, label="Dam axis"),
                       plt.Line2D([], [], color="#7a5c3a", lw=0.8, label=f"Contours ({u.length(step)})")],
              loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=7, frameon=False)
    return _png(fig)


def fig_monthly(res, u):
    K = res["climate"]
    f = MM_TO_IN if u.imp else 1
    unit = "in" if u.imp else "mm"
    fig, ax = plt.subplots(figsize=(6.6, 2.5))
    m = np.arange(12)
    ax.bar(m, np.array(K["monthly_precip_mm"]) * f, color=BLUE, width=0.6, label="Rainfall")
    ax.plot(m, np.array(K["monthly_pet_mm"]) * f, color=ORANGE, lw=2, marker="o", ms=4, label="Potential evapotranspiration")
    ax.set_xticks(m, list("JFMAMJJASOND"))
    ax.set_ylabel(f"{unit} per month")
    ax.legend(fontsize=7.5, frameon=False, loc="upper left")
    ax.grid(axis="x", visible=False)
    return _png(fig)


def fig_annual(res, u):
    K, Y = res["climate"], res.get("yield")
    f = MM_TO_IN if u.imp else 1
    unit = "in" if u.imp else "mm"
    fig, ax = plt.subplots(figsize=(6.6, 2.5))
    yrs = np.array(K["years"])
    ax.bar(yrs, np.array(K["annual_precip"]) * f, color="#9cc7ec", width=0.7, label="Rainfall")
    if Y:
        ax.bar(yrs, np.array(Y["annual_runoff_series_mm"]) * f, color="#1f5fa8", width=0.7, label="Runoff (water balance)")
    ax.set_ylabel(f"{unit} per year")
    ax.legend(fontsize=7.5, frameon=False, loc="upper left", ncol=2)
    ax.grid(axis="x", visible=False)
    return _png(fig)


def fig_storage(res, scen, u):
    cv = res["site_analysis"]["curve"]
    H = np.array([e["H"] for e in cv], float) * (M_TO_FT if u.imp else 1)
    V = np.array([e["volume_mcm"] for e in cv]) * (MCM_TO_ACFT if u.imp else 1)
    fig, ax = plt.subplots(figsize=(3.3, 2.6))
    ax.plot(H, V, color=BLUE, lw=2, marker="o", ms=3.5)
    sh = scen["H"] * (M_TO_FT if u.imp else 1)
    sv = scen["chosen"]["volume_mcm"] * (MCM_TO_ACFT if u.imp else 1)
    ax.plot([sh], [sv], marker="o", ms=8, color="#d62828", zorder=5)
    ax.set_xlabel(f"Dam height ({'ft' if u.imp else 'm'})")
    ax.set_ylabel("Storage (" + ("acre-ft" if u.imp else "million m³") + ")")
    ax.set_title("Storage vs dam height", fontsize=8.5, loc="left")
    return _png(fig)


def fig_xs(res, scen, u):
    xs = res["cross_section"]
    f = M_TO_FT if u.imp else 1
    d = np.array(xs["distance_m"]) * f
    z = np.array(xs["elev_m"]) * f
    fig, ax = plt.subplots(figsize=(3.3, 2.6))
    ax.fill_between(d, z, z.min() - 2 * f, color="#d9c7ad", lw=0)
    ax.plot(d, z, color="#7a5c3a", lw=1.2)
    x = scen["xs"]
    ax.plot([x["left_m"] * f, x["right_m"] * f], [x["crest_elev"] * f] * 2, color="#d62828", lw=2, label="Dam crest")
    ax.plot([x["left_m"] * f, x["right_m"] * f], [x["fsl"] * f] * 2, color=BLUE, lw=1.5, ls="--", label="Full supply level")
    span = (x["right_m"] - x["left_m"]) * f
    ax.set_xlim(x["left_m"] * f - span, x["right_m"] * f + span)
    win = (d > x["left_m"] * f - span) & (d < x["right_m"] * f + span)
    if win.any():
        ax.set_ylim(z[win].min() - 2 * f, max(z[win].max(), x["crest_elev"] * f) + 5 * f)
    unit = "ft" if u.imp else "m"
    ax.set_xlabel(f"Distance across valley ({unit})")
    ax.set_ylabel(f"Elevation ({unit})")
    ax.set_title("Valley cross-section at the dam", fontsize=8.5, loc="left")
    ax.legend(fontsize=6.5, frameon=False, loc="upper center")
    return _png(fig)


def fig_groups(assess):
    G = [g for g in assess["groups"]]
    fig, ax = plt.subplots(figsize=(6.6, 0.45 * len(G) + 0.5))
    labels = [g["label"] for g in G][::-1]
    vals = [g["score"] or 0 for g in G][::-1]
    cols = [_score_color(v) for v in vals]
    ax.barh(labels, [100] * len(vals), color="#eef1f4", height=0.55)
    ax.barh(labels, vals, color=cols, height=0.55)
    for i, v in enumerate(vals):
        ax.text(v + 1.5, i, f"{v:.0f}", va="center", fontsize=8.5, color=INK, fontweight="bold")
    ax.set_xlim(0, 108)
    ax.set_xticks([0, 20, 40, 60, 80, 100])
    ax.grid(axis="y", visible=False)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0, labelsize=8.5)
    return _png(fig)


def _score_color(s):
    return "#2a9d5c" if s >= 75 else "#8ab83a" if s >= 55 else "#e0a800" if s >= 35 else "#d62828"


# ------------------------------------------------------------------ document
def build_report(res, R, units="metric", height=None):
    u = U(units)
    scen_list = res.get("scenarios") or []
    idx = res.get("default_scenario", 0)
    if height is not None and scen_list:
        idx = min(range(len(scen_list)), key=lambda i: abs(scen_list[i]["H"] - height))
    scen = scen_list[idx] if scen_list else None
    A = scen["assessment"] if scen else res["assessment"]
    ch = scen["chosen"] if scen else res.get("chosen")
    soc = scen["social"] if scen else res.get("social")
    sed = scen["sediment"] if scen else res.get("sediment")
    mode = res["input"]["mode"]
    mode_name = "Storage dam" if mode == "dam" else "Water harvesting structure"

    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontName="DejaVu", fontSize=9.3, leading=13, textColor=INK,
                          alignment=TA_LEFT, spaceAfter=4)
    small = ParagraphStyle("s", parent=body, fontSize=7.8, leading=10, textColor=MUTED)
    ref = ParagraphStyle("r", parent=body, fontSize=8, leading=10.5, leftIndent=12, firstLineIndent=-12, spaceAfter=3)
    h1 = ParagraphStyle("h1", parent=body, fontName="DejaVu-Bold", fontSize=18, leading=22, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=body, fontName="DejaVu-Bold", fontSize=12.5, leading=16, spaceBefore=10,
                        spaceAfter=5, textColor=ACCENT)
    h3 = ParagraphStyle("h3", parent=body, fontName="DejaVu-Bold", fontSize=10, leading=13, spaceBefore=6, spaceAfter=3)
    cap = ParagraphStyle("c", parent=small, fontName="DejaVu-Oblique", spaceBefore=2, spaceAfter=8)

    def tbl(rows, widths, header=True, zebra=True):
        data = [[Paragraph(str(c), small if (header and i == 0) else body) for c in r] for i, r in enumerate(rows)]
        t = Table(data, colWidths=[w * cm for w in widths], hAlign="LEFT")
        st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e7ec")),
              ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        if header:
            st += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3f8"))]
        t.setStyle(TableStyle(st))
        return t

    def kv(rows):
        return tbl([[k, v] for k, v in rows if v is not None], [7.5, 9.5], header=False)

    C, Y, K, F, W, O, Q = (res["catchment"], res.get("yield"), res.get("climate"), res.get("flood"),
                           res.get("water_bodies"), res.get("osm"), res.get("seismic"))
    story = []

    # ---------------- title
    site_name = (res.get("input") or {}).get("name")
    story.append(Paragraph("Water Storage Site Finder: Site Screening Report", h1))
    if site_name:
        story.append(Paragraph(site_name.replace("&", "&amp;").replace("<", "&lt;"),
                               ParagraphStyle("nm", parent=h2, fontSize=15, leading=19, spaceBefore=2, textColor=INK)))
    story.append(Paragraph(f"{mode_name} · site {res['site']['lat']:.5f}°, {res['site']['lon']:.5f}° · "
                           f"generated {dt.date.today().isoformat()} · analysis v{res.get('version', '1')} · "
                           f"units: {u.system}", small))
    story.append(Paragraph(f"Prepared with {APP_NAME}, developed by {AUTHOR}", small))
    story.append(Spacer(1, 8))
    sc = A["score"]
    big = ParagraphStyle("big", parent=body, leading=34)
    head = Table([[Paragraph(f"<font size=30><b>{sc:.0f}</b></font><font size=11 color='{MUTED}'> / 100</font>", big),
                   Paragraph(f"<font size=15 color='{_score_color(sc)}'><b>{A['rating']}</b></font><br/>"
                             f"<font size=8.5 color='{MUTED}'>Dam height assessed: {u.height(ch['H']) if ch else '–'} · "
                             f"data confidence {A['data_confidence']}%</font>", body)]],
                 colWidths=[4.2 * cm, 12.8 * cm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f7fa")),
                              ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dfe5eb")),
                              ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story.append(head)

    # ---------------- summary
    story.append(Paragraph("1. Summary", h2))
    from app.analysis.scoring import strengths_and_concerns
    strengths, concerns = strengths_and_concerns(A)
    txt = (f"The selected site{' (' + site_name.replace('&', '&amp;').replace('<', '&lt;') + ')' if site_name else ''} drains a catchment of <b>{u.area(C['area_km2'])}</b>"
           + (" (a lower bound, as the basin extends beyond the analysis window)" if C.get("truncated") else "")
           + (f" that receives about <b>{u.depth(K['mean_annual_precip_mm'])}</b> of rain a year "
              f"({K['climate_class']} climate)" if K else "") + ". ")
    if Y:
        txt += f"On average the river delivers about <b>{u.vol(Y['mean_annual_runoff_mcm'])}</b> of water a year. "
    if ch:
        txt += (f"A {mode_name.lower()} <b>{u.length(ch['H'])}</b> high with a crest about {u.length(ch['crest_length_m'])} "
                f"long would store roughly <b>{u.vol(ch['volume_mcm'])}</b> over {u.area(ch['area_km2'])}. ")
    txt += f"Overall the site scores <b>{sc:.0f}/100, {A['rating'].lower()}</b>."
    story.append(Paragraph(txt, body))
    if strengths:
        story.append(Paragraph("<b>Main strengths</b>", body))
        for f in strengths:
            story.append(Paragraph(f"• <b>{f['label']}</b>: {f['plain'][u.system]}", body))
    if concerns or A["flags"]:
        story.append(Paragraph("<b>Main concerns</b>", body))
        for fl in A["flags"]:
            if fl["level"] != "info":
                story.append(Paragraph(f"• <font color='#b42318'><b>{fl['text']}</b></font>", body))
        for f in concerns:
            story.append(Paragraph(f"• <b>{f['label']}</b>: {f['plain'][u.system]}", body))
    story.append(Spacer(1, 4))
    story.append(_img(fig_groups(A), 15.5))
    story.append(Paragraph("Theme scores (0–100). Each theme combines several factors; details in Section 9.", cap))

    # ---------------- catchment
    story.append(PageBreak())
    story.append(Paragraph("2. Catchment and water bodies", h2))
    story.append(_img(fig_catchment(R, res, u), 15.5))
    story.append(Paragraph(f"Figure 1. Catchment draining to the selected site, with main streams and mapped surface water. "
                           f"Background: hillshade of the {C['resolution_m']:.0f} m Copernicus DEM.", cap))
    story.append(kv([
        ("Catchment area", u.area(C["area_km2"]) + (" (lower bound)" if C.get("truncated") else "")),
        ("Elevation range", f"{u.elev(C['elev_min_m'])} – {u.elev(C['elev_max_m'])} (mean {u.elev(C['elev_mean_m'])})"),
        ("Mean slope", f"{C['mean_slope_pct']:.1f} %"),
        ("Longest flow path", u.dist(C["longest_flow_path_km"])),
        ("Site elevation (streambed)", u.elev(res["site"].get("streambed_elev_m", res["site"]["elevation_m"]))),
    ]))
    story.append(Paragraph("Water bodies inside the catchment", h3))
    if W and W["count"]:
        story.append(Paragraph(
            f"{W['count']} surface-water bod{'y' if W['count'] == 1 else 'ies'} covering {u.area(W['total_area_km2'])} "
            f"({u.area(W['permanent_area_km2'])} permanent) were found inside the catchment. Source: {W['source']}.", body))
        rows = [["#", "Type", "Area", "Distance from site", "Water present"]]
        for i, b in enumerate(W["bodies"][:12], 1):
            rows.append([i, b["type"], u.area(b["area_km2"]), u.dist(b["distance_km"]), f"{b['mean_occurrence']:.0f}% of time"])
        story.append(tbl(rows, [1, 3, 3.5, 4, 4]))
    elif W:
        story.append(Paragraph(f"No surface water was mapped inside the catchment (source: {W['source']}).", body))
    else:
        story.append(Paragraph("Surface-water data were unavailable.", body))
    if O and O["named_water"]:
        story.append(Paragraph("Named rivers and lakes (OpenStreetMap): " +
                               ", ".join(f"{w['name']} ({w['kind']})" for w in O["named_water"][:12]) + ".", body))
    if O and O["existing_dams"]:
        story.append(Paragraph("Existing dams / weirs mapped in the catchment: " +
                               ", ".join(f"{d['name'] or d['type']} ({u.dist(d['distance_km'])} away)" for d in O["existing_dams"][:8]) + ".", body))

    # ---------------- terrain
    fig_n = 2
    story.append(PageBreak())
    story.append(Paragraph("3. Terrain", h2))
    for name, title, expl in [("elevation", "Elevation and relief", "Elevation inside the catchment, shaded to show relief."),
                              ("slope", "Slope", "Steeper ground sheds water faster and erodes more easily.")]:
        if name == "slope" or R.get("dem") is not None:
            story.append(KeepTogether([_img(fig_layer(R, name, u), 14.5),
                                       Paragraph(f"Figure {fig_n}. {title}. {expl}", cap)]))
            fig_n += 1

    # ---------------- land cover
    story.append(PageBreak())
    story.append(Paragraph("4. Land cover and change", h2))
    if res.get("landcover_named"):
        story.append(Paragraph("Current land cover (ESA WorldCover 2021, 10 m): " + ", ".join(
            f"{c['name']} {c['pct']:.0f}%" for c in res["landcover_named"] if c["pct"] >= 0.5) + ".", body))
    if R.get("lc") is not None:
        story.append(KeepTogether([_img(fig_layer(R, "landcover", u), 14),
                                   Paragraph(f"Figure {fig_n}. Land cover in 2021.", cap)]))
        fig_n += 1
    def change_block(c, title, layer, have):
        nonlocal fig_n
        if not c:
            return
        story.append(Paragraph(title, h3))
        if not c.get("detected"):
            story.append(Paragraph("No change large enough to detect at this map resolution. This is treated as "
                                   "<b>neutral</b> in the score; it does not prove the catchment is stable.", body))
        rows = [["Class", str(c["from_year"]), str(c["to_year"]), "Change (pp)"]]
        for gname in c["new_pct"]:
            if max(c["old_pct"][gname], c["new_pct"][gname]) >= 0.5:
                rows.append([gname, f"{c['old_pct'][gname]:.1f}%", f"{c['new_pct'][gname]:.1f}%",
                             f"{c['change_pp'][gname]:+.1f}"])
        story.append(tbl(rows, [6, 3, 3, 3]))
        if have and c.get("detected"):
            story.append(KeepTogether([_img(fig_layer(R, layer, u), 14),
                                       Paragraph(f"Figure {fig_n}. Where land cover changed, {c['from_year']}–{c['to_year']}.", cap)]))
            fig_n += 1

    change_block(res.get("landcover_change_recent"), "Recent change (Impact Observatory / Esri, 10 m, 2017–2023)",
                 "lcchange10", R.get("io_new") is not None)
    change_block(res.get("landcover_change"), "Long-term change (ESA CCI Land Cover, 300 m, 1992–2020)",
                 "lcchange", R.get("cci_new") is not None)

    # ---------------- climate & hydrology
    story.append(PageBreak())
    story.append(Paragraph("5. Climate and hydrology", h2))
    if K:
        story.append(kv([
            ("Mean annual rainfall", u.depth(K["mean_annual_precip_mm"])),
            ("Mean annual potential evapotranspiration", u.depth(K["mean_annual_pet_mm"])),
            ("Aridity index (rainfall ÷ PET)", f"{K['aridity_index']:.2f} ({K['climate_class']})"),
            ("Year-to-year rainfall variability (CV)", f"{K['precip_cv']:.2f}"),
            ("Mean annual runoff", Y and f"{u.depth(Y['mean_annual_runoff_mm'])} = {u.vol(Y['mean_annual_runoff_mcm'])}"),
            ("Dependable runoff (3 years in 4)", Y and u.vol(Y["dependable_runoff_mcm"])),
            ("Storm runoff (SCS curve number)", Y and f"{u.depth(Y['scs_quickflow_mm'])} = {u.vol(Y['scs_quickflow_mcm'])}"),
            ("Curve number / soil group", f"{res['runoff_params']['curve_number']:.0f} / {res['runoff_params']['hsg']}"),
            ("100-year flood peak (indicative)", F and u.flow(F["peak_m3s"])),
        ]))
        FT = res.get("floods")
        if FT and FT.get("rows"):
            story.append(Paragraph("Flood peaks for different return periods (indicative)", h3))
            rows = [["Flood", "Peak flow", "24-hour rainfall", "Storm runoff", "Note"]]
            for r in FT["rows"]:
                rows.append([f"1-in-{r['T']:,} year", u.flow(r["peak_m3s"]), u.depth(r["rain_24h_mm"]), u.depth(r["runoff_mm"]),
                             ("Usual design flood" if r["design"] else "") + (" (extrapolated)" if r["extrapolated"] else "")])
            story.append(tbl(rows, [3, 3, 3.2, 3, 4.8]))
            story.append(Paragraph(
                ("The usual design flood for the spillway of a small or medium earth dam is the 1-in-1,000 year flood; "
                 "dams whose failure would endanger lives downstream are normally designed for the Probable Maximum Flood. "
                 if mode == "dam" else
                 "Check dams, bunds and ponds are usually designed for the 1-in-25 year flood (1-in-50 to 1-in-100 where "
                 "failure would damage roads, houses or farmland). ") + FT["note"], body))
        story.append(_img(fig_monthly(res, u), 15.5))
        story.append(Paragraph(f"Figure {fig_n}. Average monthly rainfall and potential evapotranspiration, "
                               f"{K['years'][0] if K.get('years') else ''}–{K['years'][-1] if K.get('years') else ''} (ERA5).", cap))
        fig_n += 1
        if K.get("years"):
            story.append(_img(fig_annual(res, u), 15.5))
            story.append(Paragraph(f"Figure {fig_n}. Annual rainfall and estimated runoff.", cap))
            fig_n += 1
    else:
        story.append(Paragraph("Climate data were unavailable.", body))

    # ---------------- dam & reservoir
    story.append(PageBreak())
    story.append(Paragraph("6. Dam and reservoir" if mode == "dam" else "6. Harvesting structure and storage", h2))
    if ch and scen:
        story.append(kv([
            ("Height (crest above streambed)", u.height(ch["H"])),
            ("Crest length", u.length(ch["crest_length_m"]) + ("" if ch["closed"] else " (valley not closed)")),
            ("Approximate embankment (earth) fill", u.fill(ch["fill_m3"])),
            ("Storage at full supply level", u.vol(ch["volume_mcm"])),
            ("Reservoir surface area", u.area(ch["area_km2"])),
            ("Mean depth", u.length(ch["mean_depth_m"], 1)),
            ("Water : fill (water stored per unit of earth fill)",
             f"{ch['wer']:.1f}, {wer_meaning(ch['wer'])}: each {u.fill_word()} of earth holds back about "
             f"{ch['wer']:.0f} {u.fill_word(True)} of water"),
            ("Freeboard allowed", u.length(ch["freeboard"], 1)),
        ]))
        site_png = fig_site(R, res, scen, u)
        if site_png:
            story.append(KeepTogether([_img(site_png, 14),
                                       Paragraph(f"Figure {fig_n}. Reservoir extent at full supply level for the "
                                                 f"{u.length(ch['H'])} dam, on 30 m terrain.", cap)]))
            fig_n += 1
        row = Table([[_img(fig_storage(res, scen, u), 8.2), _img(fig_xs(res, scen, u), 8.2)]],
                    colWidths=[8.5 * cm, 8.5 * cm])
        story.append(row)
        story.append(Paragraph(f"Figure {fig_n}. Left: storage gained at each dam height (red = height assessed). "
                               f"Right: valley profile at the dam axis.", cap))
        fig_n += 1
        rows = [["Height", "Crest length", "Storage", "Area", "Water : fill", "Score"]]
        for s in scen_list:
            e = s["chosen"]
            rows.append([u.height(e["H"]), u.length(e["crest_length_m"]), u.vol(e["volume_mcm"]), u.area(e["area_km2"]),
                         f"{e['wer']:.1f} ({wer_meaning(e['wer'])})", f"{s['assessment']['score']:.0f}"])
        story.append(Paragraph("All heights tested", h3))
        story.append(Paragraph("<i>Water : fill</i> = volume of water stored ÷ volume of earth in the embankment "
                               f"(same in any units). Guide: {WER_GUIDE}.", body))
        story.append(tbl(rows, [2.2, 2.6, 3.2, 2.6, 3.9, 1.5]))
    hs = res.get("harvest_sites")
    if mode == "harvest":
        story.append(Paragraph("Other water-harvesting sites nearby", h3))
        if hs:
            story.append(Paragraph("Small side gullies within about 3 km of the selected point, with catchments of "
                                   "0.1–5 km², tested with a structure "
                                   f"{u.length(hs[0]['H'], 1)} high. Ranked by water stored per unit of fill, how well "
                                   "storm runoff fills them, and ground slope.", body))
            rows = [["#", "Location (lat, lon)", "Catchment", "Storage", "Crest", "Water : fill", "Rank score"]]
            for i, h_ in enumerate(hs, 1):
                rows.append([i, f"{h_['lat']:.5f}, {h_['lon']:.5f}", u.area(h_["catchment_km2"]), u.vol(h_["volume_mcm"]),
                             u.length(h_["crest_length_m"]), f"{h_['wer']:.1f}", f"{h_['score']:.0f}"])
            story.append(tbl(rows, [0.8, 4.2, 2.6, 3, 2.2, 2.2, 2]))
        else:
            story.append(Paragraph("No suitable small side gullies were found within about 3 km.", body))

    # ---------------- sediment
    story.append(PageBreak())
    story.append(Paragraph("7. Erosion and sedimentation", h2))
    if sed:
        story.append(kv([
            ("Mean soil loss (RUSLE)", u.rate(sed["mean_soil_loss_t_ha_yr"])),
            ("Sediment delivery ratio", f"{sed['sdr']:.2f}"),
            ("Sediment reaching the site", u.mass(sed["sediment_yield_t_yr"]) + " per year"),
            ("Reservoir trap efficiency", f"{100 * (sed.get('trap_efficiency') or 0):.0f}%"),
            ("Years until half the storage is silted",
             "> 500" if (sed.get("life_50pct_years") or 0) > 500 else f"{sed.get('life_50pct_years') or 0:.0f}"),
        ]))
        if R.get("soil_loss") is not None:
            story.append(KeepTogether([_img(fig_layer(R, "erosion", u), 14),
                                       Paragraph(f"Figure {fig_n}. Estimated soil loss (t/ha/yr). Hot spots are candidates for "
                                                 f"catchment treatment (terracing, check dams, re-vegetation).", cap)]))
            fig_n += 1

    # ---------------- people & hazards
    story.append(Paragraph("8. People, environment and hazards", h2))
    rows = []
    if soc:
        rows += [("Built-up land flooded", u.area(soc["builtup_km2"])), ("Farmland flooded", u.area(soc["cropland_km2"])),
                 ("Tree cover flooded", u.area(soc["tree_km2"])),
                 ("Mapped settlements in reservoir", ", ".join(soc.get("settlements_flooded") or []) or "none")]
    if O:
        rows += [("Inside a protected area", ", ".join(O["protected_inside"]) or "No"),
                 ("Protected areas within 10 km", ", ".join(p["name"] for p in O["protected_nearby"][:4]) or "none mapped"),
                 ("Nearest settlements", ", ".join(f"{p['name'] or p['type']} ({u.dist(p['distance_km'])})" for p in O["places"][:4]) or "–")]
    if Q:
        rows += [("Earthquakes M≥5 within 100 km since 1970", str(Q["n_m5_100km"])),
                 ("Largest within 150 km", f"M{Q['largest']['mag']:.1f}, {u.dist(Q['largest']['distance_km'])} away" if Q.get("largest") else "–")]
    if res.get("soil"):
        s_ = res["soil"]
        rows.append(("Soil texture (0–60 cm)", f"clay {s_['clay']:.0f}%, sand {s_['sand']:.0f}%, silt {s_['silt']:.0f}%"))
    story.append(kv(rows))

    # ---------------- score
    story.append(PageBreak())
    story.append(Paragraph("9. How the score was worked out", h2))
    story.append(Paragraph("Each factor below is rated 0–100 from the measured value, then combined using the weights shown. "
                           "Factors without data are left out and the other weights scaled up. Serious problems "
                           "(“fatal flags”) cap the total score.", body))
    for gp in A["groups"]:
        story.append(Paragraph(f"{gp['label']}: {gp['verdict']}" + (f" ({gp['score']:.0f}/100)" if gp["score"] is not None else ""), h3))
        story.append(Paragraph(f"<i>{gp['question']}</i>", small))
        rows = [["Factor", "Rating", "What it means", "Weight"]]
        for f in A["factors"]:
            if f["group"] == gp["key"]:
                rows.append([f["label"], f"{f['verdict']}" + (f" ({f['score']:.0f})" if f["available"] else ""),
                             f["plain"][u.system], str(f["weight"])])
        story.append(tbl(rows, [3.6, 2.4, 9.6, 1.4]))

    # ---------------- methods & limits
    story.append(Paragraph("10. Methods, assumptions and limitations", h2))
    for t in [
        "<b>Terrain & catchment.</b> The catchment is traced on the Copernicus DEM using D8 flow directions after filling "
        "depressions and routing flats. The site is snapped to the nearest channel. Large basins are analysed on coarser "
        "grids (90–800 m).",
        "<b>Water balance.</b> Long-term runoff uses the Budyko–Fu equation (w = 2.6) with ERA5 rainfall and FAO-56 reference "
        "evapotranspiration. Storm runoff uses the SCS curve number method with land cover from ESA WorldCover and soil "
        "groups from SoilGrids texture. Flood peaks (2- to 1,000-year) are Gumbel estimates of daily rainfall applied through the SCS "
        "unit hydrograph with Kirpich time of concentration. Indicative only, as gridded rainfall understates local extremes.",
        "<b>Dam & reservoir.</b> For each height the crest is placed across the valley perpendicular to the stream. Fill volume "
        "assumes an earth embankment (crest width 3 m + H/5, side slopes 3:1 and 2.5:1). Storage is the DEM volume below "
        "full supply level (crest minus freeboard) connected to the site.",
        "<b>Sediment.</b> RUSLE soil loss with a Vanoni delivery ratio and Brune trap efficiency; a deposit density of "
        "1.3 t/m³ is assumed.",
        "<b>Limitations.</b> This is a desk-based screening from global open datasets with 10–300 m resolution. Results "
        "do not replace topographic survey, hydrological gauging, geotechnical and geological investigation, "
        "environmental and social impact assessment, or detailed engineering design.",
    ]:
        story.append(Paragraph(t, body))
    st = res.get("data_status", {})
    if st:
        story.append(Paragraph("Data retrieved for this site", h3))
        story.append(kv([(k, v) for k, v in st.items()]))

    # ---------------- references
    story.append(PageBreak())
    story.append(Paragraph("References and data sources", h2))
    for i, (_, r) in enumerate(REFERENCES, 1):
        story.append(Paragraph(f"[{i}] {r}", ref))
    story.append(Paragraph("How to cite this report", h3))
    story.append(Paragraph(
        f"Ahmad, J. ({dt.date.today().year}). <i>{APP_NAME}</i> (version {res.get('version', '1')}) "
        f"[software]. Screening report for site {res['site']['lat']:.4f}, {res['site']['lon']:.4f}, "
        f"generated {dt.date.today().isoformat()}.", ref))
    story.append(Paragraph(f"{COPYRIGHT}. Screening results only, not a substitute for survey, "
                           "geotechnical investigation or hydrological design.", ref))

    buf = io.BytesIO()
    site_txt = (f"{site_name} · " if site_name else "") + f"{res['site']['lat']:.4f}, {res['site']['lon']:.4f}"

    def deco(canvas, doc):
        canvas.saveState()
        canvas.setFont("DejaVu", 7.5)
        canvas.setFillColor(colors.HexColor(MUTED))
        canvas.drawString(1.8 * cm, 1.1 * cm, f"{APP_NAME} · site {site_txt} · {COPYRIGHT}")
        canvas.drawRightString(A4[0] - 1.8 * cm, 1.1 * cm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.6 * cm,
                            bottomMargin=1.8 * cm, title=f"Water Storage Site Finder screening report{' - ' + site_name if site_name else ''}", author=AUTHOR, creator=f"{APP_NAME}, {COPYRIGHT}")
    doc.build(story, onFirstPage=deco, onLaterPages=deco)
    return buf.getvalue()
