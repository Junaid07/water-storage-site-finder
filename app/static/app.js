/* SPDX-License-Identifier: AGPL-3.0-or-later. Copyright (C) 2026 Junaid Ahmad, PhD */
/* Water Storage Site Finder front-end */
(() => {
  const $ = (s) => document.querySelector(s);
  const state = { mode: "dam", units: "metric", point: null, R: null, idx: 0, layer: "none", opacity: 0.75,
                  base: {}, scen: {}, overlay: null, charts: {}, layersList: [] };
  try { state.units = localStorage.getItem("df_units") || "metric"; } catch (e) { /* ignore */ }

  // ------------------------------------------------------------------ units
  const NUM = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v)) ? "–" :
    Number(v).toLocaleString(undefined, { maximumFractionDigits: Math.abs(v) >= 100 ? 0 : d, minimumFractionDigits: 0 });
  const U = {
    get imp() { return state.units === "imperial"; },
    length(m, d = 0) { return this.imp ? `${NUM(m * 3.28084, d)} ft` : `${NUM(m, d)} m`; },
    elev(m) { return this.length(m, 0); },
    height(m) { return this.length(m, m < 10 ? 1 : 0); },
    dist(km, d = 1) { return this.imp ? `${NUM(km * 0.621371, d)} mi` : `${NUM(km, d)} km`; },
    area(km2) {
      if (km2 === null || km2 === undefined) return "–";
      if (this.imp) return km2 * 0.386102 >= 1 ? `${NUM(km2 * 0.386102, 2)} mi²` : `${NUM(km2 * 247.105, 0)} acres`;
      return km2 >= 1 ? `${NUM(km2, 2)} km²` : `${NUM(km2 * 100, 1)} ha`;
    },
    vol(mcm) {
      if (mcm === null || mcm === undefined) return "–";
      if (this.imp) return `${NUM(mcm * 810.713, 0)} acre-ft`;
      return mcm < 0.1 ? `${NUM(mcm * 1e6, 0)} m³` : `${NUM(mcm, 2)} million m³`;
    },
    fill(m3) { return this.imp ? `${NUM(m3 * 35.3147, 0)} ft³` : `${NUM(m3, 0)} m³`; },
    fillWord(pl) { return this.imp ? (pl ? "cubic feet" : "cubic foot") : (pl ? "cubic metres" : "cubic metre"); },
    flow(c) { return this.imp ? `${NUM(c * 35.3147, 0)} cfs` : `${NUM(c, 0)} m³/s`; },
    depth(mm) { return this.imp ? `${NUM(mm * 0.0393701, 1)} in` : `${NUM(mm, 0)} mm`; },
    mass(t) { return this.imp ? `${NUM(t * 1.10231, 0)} US tons` : `${NUM(t, 0)} t`; },
    rate(x) { return this.imp ? `${NUM(x * 0.44609, 2)} tons/acre/yr` : `${NUM(x, 2)} t/ha/yr`; },
    // chart scale factors
    fLen() { return this.imp ? 3.28084 : 1; }, uLen() { return this.imp ? "ft" : "m"; },
    fVol() { return this.imp ? 810.713 : 1; }, uVol() { return this.imp ? "acre-ft" : "million m³"; },
    fDepth() { return this.imp ? 0.0393701 : 1; }, uDepth() { return this.imp ? "in" : "mm"; },
  };

  const werMeaning = (w) => (w === null || w === undefined) ? "–" :
    w >= 30 ? "very efficient" : w >= 10 ? "efficient" : w >= 3 ? "average" : "expensive";

  // ------------------------------------------------------------------ map
  const map = L.map("map", { zoomControl: true }).setView([30, 70], 5);
  window.dfMap = map;
  // full-screen map toggle (hides the side panel)
  const FsCtl = L.Control.extend({
    options: { position: "topleft" },
    onAdd() {
      const d = L.DomUtil.create("div", "leaflet-bar");
      const a = L.DomUtil.create("a", "mapfs", d);
      a.href = "#"; a.title = "Full-screen map"; a.setAttribute("role", "button"); a.innerHTML = "⤢";
      L.DomEvent.on(a, "click", (e) => {
        L.DomEvent.preventDefault(e); L.DomEvent.stopPropagation(e);
        const on = document.body.classList.toggle("mapfull");
        a.innerHTML = on ? "⤡" : "⤢"; a.title = on ? "Exit full-screen map" : "Full-screen map";
        setTimeout(() => map.invalidateSize(), 50);
      });
      return d;
    },
  });
  map.addControl(new FsCtl());
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.body.classList.contains("mapfull") && !state.fsOpen) {
      document.body.classList.remove("mapfull"); const a = document.querySelector(".mapfs"); if (a) a.innerHTML = "⤢";
      setTimeout(() => map.invalidateSize(), 50);
    }
  });
  const topo = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", { maxZoom: 17, attribution: "© OpenTopoMap (CC-BY-SA), © OpenStreetMap" });
  const sat = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, attribution: "Imagery © Esri" });
  const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap contributors" });
  topo.addTo(map);
  const ctrl = L.control.layers({ "Topographic": topo, "Satellite": sat, "OpenStreetMap": osm }, {}, { collapsed: true }).addTo(map);
  L.control.scale({ imperial: true, metric: true }).addTo(map);
  map.createPane("raster"); map.getPane("raster").style.zIndex = 350;

  let clickMarker = null;
  function setPoint(lat, lon) {
    state.point = { lat, lon };
    if (clickMarker) clickMarker.remove();
    clickMarker = L.marker([lat, lon]).addTo(map);
    $("#picked").textContent = `Selected: ${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    $("#run").disabled = false;
  }
  state.tab = "site";
  map.on("click", (e) => { if (state.tab !== "site" || state.draw) return; setPoint(e.latlng.lat, e.latlng.lng); });
  // ---- location finder: coordinates (decimal or degrees-minutes-seconds) or a place name
  function parseCoords(txt) {
    const t = txt.trim();
    if (!t) return null;
    // degrees / minutes / seconds with hemisphere letters, e.g. 33°01'32"N 72°46'08"E
    const dms = [...t.matchAll(/(-?\d+(?:\.\d+)?)\s*[°d:\s]\s*(?:(\d+(?:\.\d+)?)\s*['′m:\s]\s*)?(?:(\d+(?:\.\d+)?)\s*["″s]?\s*)?([NSEWnsew])/g)];
    if (dms.length === 2) {
      const v = dms.map((m) => {
        let x = Math.abs(+m[1]) + (+m[2] || 0) / 60 + (+m[3] || 0) / 3600;
        if (/[SWsw]/.test(m[4]) || +m[1] < 0) x = -x;
        return { x, ns: /[NSns]/.test(m[4]) };
      });
      const lat = v[0].ns ? v[0].x : v[1].x, lon = v[0].ns ? v[1].x : v[0].x;
      return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? [lat, lon] : null;
    }
    if (/^[\s\d.,;+-]+$/.test(t)) {
      const m = t.split(/[ ,;]+/).filter(Boolean).map(Number);
      if (m.length === 2 && m.every((v) => !isNaN(v)) && Math.abs(m[0]) <= 90 && Math.abs(m[1]) <= 180) return m;
    }
    return null;
  }
  let placeMarker = null, geoTimer = null, geoSeq = 0, geoResults = [], geoActive = -1;
  const geoList = $("#geoList");
  function hideGeo() { geoList.classList.add("hidden"); geoActive = -1; }
  function showPlace(p) {
    hideGeo();
    $("#coord").value = p.name.split(",").slice(0, 3).join(",");
    if (placeMarker) placeMarker.remove();
    placeMarker = L.circleMarker([p.lat, p.lon], { radius: 7, color: "#fff", weight: 2, fillColor: "#7c3aed", fillOpacity: 1 })
      .bindTooltip(p.name.split(",")[0], { permanent: true, direction: "top", offset: [0, -8], className: "placetip" }).addTo(map);
    syncClear();
    if (p.bbox) {
      const [s_, n_, w_, e_] = p.bbox;
      const big = (n_ - s_) > 0.02 || (e_ - w_) > 0.02;
      if (big) { map.fitBounds([[s_, w_], [n_, e_]], { maxZoom: 13 }); return; }
    }
    map.setView([p.lat, p.lon], 13);
  }
  function renderGeo(items, msg) {
    geoResults = items; geoActive = -1;
    geoList.innerHTML = msg ? `<li class="msg">${msg}</li>` : items.map((r, i) =>
      `<li role="option" data-i="${i}"><b>${esc(r.name.split(",")[0])}</b><span>${esc(r.name.split(",").slice(1).join(",").trim())}${r.kind ? ` · ${esc(r.kind)}` : ""}</span></li>`).join("")
      + `<li class="attr">Search by OpenStreetMap Nominatim</li>`;
    geoList.classList.remove("hidden");
    geoList.querySelectorAll("li[data-i]").forEach((li) => li.addEventListener("mousedown", (e) => {
      e.preventDefault(); showPlace(geoResults[+li.dataset.i]);
    }));
  }
  async function searchPlaces(q, pickFirst) {
    const seq = ++geoSeq;
    renderGeo([], "Searching…");
    try {
      const r = await fetch(`/api/geocode?q=${encodeURIComponent(q)}`);
      const js = await r.json();
      if (seq !== geoSeq) return;
      if (!r.ok) { renderGeo([], esc(js.detail || "Place search failed.")); return; }
      if (!js.results.length) { renderGeo([], "No places found. Try adding the district or country."); return; }
      if (pickFirst) showPlace(js.results[0]); else renderGeo(js.results);
    } catch (e) { if (seq === geoSeq) renderGeo([], "Place search needs an internet connection."); }
  }
  function goSearch() {
    const txt = $("#coord").value;
    const c = parseCoords(txt);
    if (c) { hideGeo(); setPoint(c[0], c[1]); map.setView(c, Math.max(map.getZoom(), 13)); return; }
    if (txt.trim().length >= 2) {
      if (!geoList.classList.contains("hidden") && geoResults.length) showPlace(geoResults[Math.max(geoActive, 0)]);
      else searchPlaces(txt.trim(), true);
    }
  }
  $("#goto").addEventListener("click", goSearch);
  $("#coord").addEventListener("input", () => {
    clearTimeout(geoTimer);
    const t = $("#coord").value.trim();
    if (t.length < 3 || parseCoords(t) || /^[\s\d.,;°'"+-]+$/.test(t)) { hideGeo(); return; }
    geoTimer = setTimeout(() => searchPlaces(t, false), 650);
  });
  $("#coord").addEventListener("keydown", (e) => {
    const items = geoList.querySelectorAll("li[data-i]");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!items.length) return;
      e.preventDefault();
      geoActive = (geoActive + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items.forEach((li, i) => li.classList.toggle("on", i === geoActive));
    } else if (e.key === "Enter") { e.preventDefault(); clearTimeout(geoTimer); goSearch(); }
    else if (e.key === "Escape") hideGeo();
  });
  $("#coord").addEventListener("blur", () => setTimeout(hideGeo, 150));

  // ---- current location (GPS / browser location)
  let meMarker = null, meCircle = null;
  $("#myLoc").addEventListener("click", () => {
    if (!navigator.geolocation) { alertMsg("Your browser cannot share its location."); return; }
    const btn = $("#myLoc"); btn.classList.add("busy");
    navigator.geolocation.getCurrentPosition((pos) => {
      btn.classList.remove("busy");
      const { latitude: lat, longitude: lon, accuracy } = pos.coords;
      if (meMarker) { meMarker.remove(); meCircle.remove(); }
      meCircle = L.circle([lat, lon], { radius: accuracy, color: "#2a78d6", weight: 1, fillOpacity: 0.08, interactive: false }).addTo(map);
      meMarker = L.circleMarker([lat, lon], { radius: 7, color: "#fff", weight: 2.5, fillColor: "#2a78d6", fillOpacity: 1 })
        .bindTooltip(`You are here (±${U.length(accuracy)})`, { direction: "top", offset: [0, -8] }).addTo(map);
      syncClear();
      map.setView([lat, lon], accuracy > 2000 ? 11 : 14);
      $("#coord").value = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
      if (state.tab === "site") setPoint(lat, lon);
    }, (err) => {
      btn.classList.remove("busy");
      alertMsg(err.code === 1 ? "Location permission was denied. Allow location access for this page in your browser settings."
        : "Could not get your location" + (location.protocol !== "https:" && location.hostname !== "localhost" ? ". Browsers only share location on https:// or localhost pages." : "."));
    }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
  });
  function alertMsg(t) {
    renderGeo([], esc(t));
    setTimeout(hideGeo, 6000);
  }
  document.querySelectorAll("#modeSeg button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#modeSeg button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on"); state.mode = b.dataset.mode;
  }));
  function syncUnitButtons() {
    document.querySelectorAll("#unitSeg button").forEach((x) => x.classList.toggle("on", x.dataset.units === state.units));
  }
  syncUnitButtons();
  document.querySelectorAll("#unitSeg button").forEach((b) => b.addEventListener("click", () => {
    state.units = b.dataset.units; syncUnitButtons();
    try { localStorage.setItem("df_units", state.units); } catch (e) { /* ignore */ }
    if (state.view === "area" && state.area) renderAreaPanel();
    else if (state.R) { renderPanel(); showLegend(); }
  }));

  // ------------------------------------------------------------------ run
  $("#run").addEventListener("click", run);
  async function run() {
    if (!state.point) return;
    $("#err").classList.add("hidden");
    $("#progress").classList.remove("hidden");
    $("#run").disabled = true;
    setProgress(1, "Submitting…");
    state.siteName = $("#siteName").value.trim();
    const body = { lon: state.point.lon, lat: state.point.lat, mode: state.mode, name: state.siteName || null,
                   snap_radius_m: $("#snap").value ? Number($("#snap").value) : null };
    try {
      const r = await fetch("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error(apiError(await r.json().catch(() => ({})), r));
      const { job_id } = await r.json();
      let j;
      while (true) {
        await new Promise((res) => setTimeout(res, 1200));
        j = await (await fetch(`/api/jobs/${job_id}`)).json();
        setProgress(j.progress, j.message);
        if (j.status !== "running") break;
      }
      if (j.status === "error") throw new Error(j.error);
      await showResult(j.result_id, state.siteName);
    } catch (e) {
      $("#err").textContent = e.message; $("#err").classList.remove("hidden");
    } finally {
      $("#progress").classList.add("hidden");
      $("#run").disabled = false;
    }
  }
  async function showResult(id, name) {
    const res = await (await fetch(`/api/results/${id}`)).json();
    res.input.name = name || res.input.name || null;
    state.R = res; state.idx = res.default_scenario || 0; state.layer = "none";
    state.layersList = await (await fetch(`/api/results/${res.id}/layers`)).json().catch(() => []);
    state.view = "site";
    renderMapBase(); renderPanel(); renderMapScenario(); setRaster("none");
  }
  function setProgress(p, msg) { $("#pbar").style.width = `${p || 0}%`; $("#pmsg").textContent = msg || ""; }

  // ------------------------------------------------------------------ helpers
  const colorFor = (s) => s >= 75 ? "#2a9d5c" : s >= 55 ? "#8ab83a" : s >= 35 ? "#e0a800" : "#d62828";
  const vColor = (v) => ({ "Neutral": "#8a96a3", "Excellent": "#2a9d5c", "Good": "#8ab83a", "Fair": "#e0a800", "Poor": "#e8792b", "Very poor": "#d62828" }[v] || "#9aa5b1");
  const kv = (rows) => `<table class="kv">${rows.filter(Boolean).map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table>`;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const scen = () => (state.R.scenarios || [])[state.idx] || null;

  function gauge(score, color) {
    const r = 46, c = 2 * Math.PI * r, off = c * (1 - score / 100);
    return `<svg class="gauge" viewBox="0 0 112 112" role="img" aria-label="Score ${Math.round(score)} of 100">
      <circle cx="56" cy="56" r="${r}" stroke="#e2e7ec" stroke-width="10" fill="none"/>
      <circle cx="56" cy="56" r="${r}" stroke="${color}" stroke-width="10" fill="none" stroke-linecap="round"
        stroke-dasharray="${c}" stroke-dashoffset="${off}" transform="rotate(-90 56 56)"/>
      <text x="56" y="60" text-anchor="middle" font-size="28" font-weight="700" fill="#1c2530">${Math.round(score)}</text>
      <text x="56" y="78" text-anchor="middle" font-size="11" fill="#5d6b7a">/ 100</text></svg>`;
  }

  // ------------------------------------------------------------------ map layers
  function clearGroup(g) { Object.values(g).forEach((l) => { if (l) { map.removeLayer(l); ctrl.removeLayer(l); } }); }
  function addLayer(group, name, gj, style, label) {
    if (!gj) return null;
    const l = L.geoJSON(gj, { style, onEachFeature: (f, lyr) => label && lyr.bindTooltip(label(f), { sticky: true }) });
    l.addTo(map); ctrl.addOverlay(l, name); group[name] = l; return l;
  }
  function renderMapBase() {
    clearGroup(state.base); state.base = {};
    const R = state.R, Ly = R.layers || {};
    const cat = addLayer(state.base, "Catchment", Ly.catchment, { color: "#e8792b", weight: 2, fillOpacity: 0.05 });
    addLayer(state.base, "Streams", Ly.streams, { color: "#2f8fd8", weight: 1.6 });
    addLayer(state.base, "Water bodies", Ly.water_bodies, { color: "#1d4ed8", weight: 1, fillColor: "#1d4ed8", fillOpacity: 0.5 },
      (f) => `${f.properties.type} water body · ${U.area(f.properties.area_km2)} · water present ${NUM(f.properties.occurrence, 0)}% of the time`);
    const site = L.circleMarker([R.site.lat, R.site.lon], { radius: 6, color: "#fff", weight: 2, fillColor: "#d62828", fillOpacity: 1 })
      .bindTooltip("Dam / structure site"); site.addTo(map); state.base.Site = site; ctrl.addOverlay(site, "Site");
    (R.harvest_sites || []).forEach((x, i) => {
      const mk = L.marker([x.lat, x.lon], { icon: L.divIcon({ className: "hsmark", html: `${"ABCDEF"[i]}`, iconSize: [22, 22] }) })
        .bindTooltip(`Nearby gully ${"ABCDEF"[i]}: ${U.vol(x.volume_mcm)}, catchment ${U.area(x.catchment_km2)}`);
      mk.on("click", () => { setPoint(x.lat, x.lon); });
      mk.addTo(map); state.base[`Harvest site ${i + 1}`] = mk;
    });
    if (cat) map.fitBounds(cat.getBounds(), { padding: [20, 20], maxZoom: 14 });
  }
  function renderMapScenario() {
    clearGroup(state.scen); state.scen = {};
    const s = scen(); if (!s) return;
    addLayer(state.scen, "Reservoir", s.reservoir, { color: "#0891b2", weight: 1, fillColor: "#36c6f4", fillOpacity: 0.6 },
      () => `Reservoir at ${U.height(s.H)} dam · ${U.vol(s.chosen.volume_mcm)}`);
    addLayer(state.scen, "Dam axis", s.dam_axis, { color: "#d62828", weight: 4 }, () => `Dam crest · ${U.length(s.chosen.crest_length_m)} long`);
  }
  async function setRaster(key) {
    state.layer = key;
    if (state.overlay) { map.removeLayer(state.overlay); state.overlay = null; }
    state.rasterMeta = null;
    if (key !== "none") {
      const id = state.R.id;
      const meta = await (await fetch(`/api/results/${id}/layer/${key}.json`)).json();
      state.rasterMeta = meta;
      state.overlay = L.imageOverlay(`/api/results/${id}/layer/${key}.png`, meta.bounds,
        { opacity: state.opacity, pane: "raster", interactive: false }).addTo(map);
    }
    showLegend();
  }
  function showLegend() {
    const el = $("#rasterLegend"); const m = state.rasterMeta;
    if (!m) { el.innerHTML = ""; return; }
    const lg = m.legend; const title = (state.layersList.find((l) => l.key === state.layer) || {}).label || "";
    let h = `<div class="lgtitle">${esc(title)}</div>`;
    if (lg.type === "classes") {
      h += lg.items.map((i) => `<div><i class="sw" style="background:${i.color}"></i>${esc(i.label)}${lg.unit ? "" : ""}</div>`).join("");
      if (lg.unit) h += `<div class="lgnote">${state.units === "imperial" && lg.unit === "t/ha/yr" ? "t/ha/yr (×0.446 = tons/acre/yr)" : lg.unit}</div>`;
    } else {
      const lo = lg.quantity === "elev" ? U.elev(lg.min) : `${NUM(lg.min, 0)}%`;
      const hi = lg.quantity === "elev" ? U.elev(lg.max) : `${NUM(lg.max, 0)}%`;
      h += `<div class="grad" style="background:linear-gradient(90deg,${lg.colors.join(",")})"></div><div class="gradlab"><span>${lo}</span><span>${hi}</span></div>`;
      if (lg.note) h += `<div class="lgnote">${esc(lg.note)}</div>`;
    }
    el.innerHTML = h;
  }

  // ------------------------------------------------------------------ panel
  function renderPanel() {
    const R = state.R, s = scen();
    const A = s ? s.assessment : R.assessment;
    const ch = s ? s.chosen : R.chosen, soc = s ? s.social : R.social, S = s ? s.sediment : R.sediment;
    const C = R.catchment, Y = R.yield, K = R.climate, F = R.flood, W = R.water_bodies, O = R.osm, Q = R.seismic, LCc = R.landcover_change;
    const sys = state.units;
    const modeName = R.input.mode === "dam" ? "Storage dam" : "Water harvesting";
    let h = "";

    // score
    if (state.area) h += `<a href="#" class="backlink" id="backArea">← Back to the ${state.area.sites.length} sites found in the area</a>`;
    if (R.input.name) h += `<div class="sitename">${esc(R.input.name)}</div>`;
    h += `<section class="card"><div class="scorecard">${gauge(A.score, colorFor(A.score))}
      <div><div class="rating" style="color:${colorFor(A.score)}">${A.rating}</div>
      <div class="meta">${modeName} · ${ch ? `${U.height(ch.H)} high` : ""} · data confidence ${A.data_confidence}%<br>
      Site ${NUM(R.site.lat, 5)}, ${NUM(R.site.lon, 5)} · ${U.elev(R.site.elevation_m)} above sea level</div></div></div>
      ${A.flags.length ? `<div class="flags">${A.flags.map((f) => `<div class="flag ${f.level}">${esc(f.text)}</div>`).join("")}</div>` : ""}
      <div class="btnrow" style="margin-top:12px">
        <button class="primary small-primary" id="dlPdf">Download PDF report</button>
        <button class="small" id="dlJson">Data (JSON)</button>
        <button class="small" id="dlGeo">Map layers (GeoJSON)</button>
      </div>
      <div class="note" id="pdfNote">The report uses the units and dam height currently shown.</div></section>`;

    // height slider
    if (R.scenarios && R.scenarios.length) {
      const n = R.scenarios.length;
      h += `<section class="card"><h2>Try a different ${R.input.mode === "dam" ? "dam" : "structure"} height</h2>
        <div class="slider"><input type="range" id="hSlider" min="0" max="${n - 1}" step="1" value="${state.idx}" aria-label="Dam height">
        <div class="ticks"><span>${U.height(R.scenarios[0].H)}</span><span>${U.height(R.scenarios[n - 1].H)}</span></div></div>
        <div class="hval">${U.height(ch.H)} <small>${state.idx === R.default_scenario ? "(suggested)" : `<a href="#" id="resetH">back to suggested ${U.height(R.scenarios[R.default_scenario].H)}</a>`}</small></div>
        <div class="tiles">
          <div class="tile"><b>${U.vol(ch.volume_mcm)}</b><span>storage</span></div>
          <div class="tile"><b>${U.area(ch.area_km2)}</b><span>lake area</span></div>
          <div class="tile"><b>${U.length(ch.crest_length_m)}</b><span>crest length</span></div>
          <div class="tile"><b>${U.fill(ch.fill_m3)}</b><span>earth fill</span></div>
          <div class="tile" title="Water stored ÷ earth fill"><b>${NUM(ch.wer, 1)}</b><span>water : fill, <em class="wer ${werMeaning(ch.wer).split(" ").pop()}">${werMeaning(ch.wer)}</em></span></div>
          <div class="tile"><b style="color:${colorFor(A.score)}">${Math.round(A.score)}</b><span>score</span></div>
        </div>
        <div class="note wernote"><b>Earth fill</b> = volume of soil needed to build the embankment. <b>Water : fill</b> = water stored ÷ earth fill: here each ${U.fillWord()} of earth holds back about ${NUM(ch.wer, 0)} ${U.fillWord(true)} of water.
          <table class="wertab"><tr><th>Water : fill</th><td>below 3</td><td>3–10</td><td>10–30</td><td>above 30</td></tr>
          <tr><th>Meaning</th><td>expensive</td><td>average</td><td>efficient</td><td>very efficient</td></tr></table></div>
        ${!ch.closed ? `<div class="flag fatal" style="margin-top:8px">The valley is not closed at this height.</div>` : ""}
        ${ch.spill ? `<div class="flag warning" style="margin-top:8px">Water would spill over a saddle at this height.</div>` : ""}
        <div class="chart"><canvas id="cEAC"></canvas></div>
        <div class="chart"><canvas id="cXS"></canvas></div>
        <div class="note">Heights above ${R.site_analysis.max_feasible_height_m ? U.length(R.site_analysis.max_feasible_height_m) : "the last one"} spill over a ridge or leave the valley open. The suggested height stores up to ${R.input.mode === "dam" ? "one year's" : "half a year's"} average inflow.</div></section>`;
    }

    // why this score
    h += `<section class="card"><h2>Why this score?</h2>
      <p class="hint">Four themes, each built from a few factors. Click a theme for the details in plain words.</p>
      ${A.groups.map((g) => `
        <details class="theme">
          <summary><div class="trow"><span class="tname">${g.label}</span><span class="chip" style="background:${vColor(g.verdict)}">${g.verdict}</span></div>
          <div class="track"><i style="width:${g.score ?? 0}%;background:${colorFor(g.score ?? 0)}"></i></div>
          <div class="tsum">${esc(g.summary)}</div></summary>
          <ul class="facts">${A.factors.filter((f) => f.group === g.key).map((f) => `
            <li class="${f.available ? "" : "na"}"><span class="dot" style="background:${vColor(f.verdict)}"></span>
              <div><b>${f.label}</b> <span class="vtxt" style="color:${vColor(f.verdict)}">${f.verdict}</span><br>${esc(f.plain[sys])}</div></li>`).join("")}</ul>
        </details>`).join("")}
      <div class="note">Weights: ${A.groups.map((g) => `${g.label.split(" ")[0].toLowerCase()} ${g.weight}%`).join(" · ")}. Missing data is left out of the score.</div></section>`;

    // water bodies in catchment
    if (W) {
      const yes = W.count > 0;
      h += `<section class="card"><h2 class="withbtn">Water bodies in the catchment <button class="small clearsel hidden" title="Clear the highlighted item">✕ Clear selection</button></h2>
        <div class="answer ${yes ? "" : "no"}">${yes
          ? `<b>Yes</b>, ${W.count} surface-water bod${W.count === 1 ? "y" : "ies"} covering ${U.area(W.total_area_km2)} (${U.area(W.permanent_area_km2)} permanent) inside the ${U.area(C.area_km2)} catchment.`
          : `<b>No</b> surface water was mapped inside the ${U.area(C.area_km2)} catchment between 1984 and 2020.`}</div>
        ${yes ? `<ul class="list">${W.bodies.slice(0, 8).map((b) => `<li class="golink" data-go="wb:${b.id}" title="Show on map">${b.type} · ${U.area(b.area_km2)} · ${U.dist(b.distance_km)} from the site · water present ${NUM(b.mean_occurrence, 0)}% of the time</li>`).join("")}</ul>` : ""}
        ${O && O.named_water.length ? `<h3>Named rivers &amp; lakes</h3><ul class="list">${O.named_water.slice(0, 15).map((w, i) => `<li class="${w.lat != null ? "golink" : ""}" data-go="nw:${i}" title="Show on map">${esc(w.name)} <span class="meta">(${esc(w.kind)})</span></li>`).join("")}</ul>` : ""}
        ${O && O.existing_dams.length ? `<h3>Existing dams / weirs in the catchment</h3><ul class="list">${O.existing_dams.map((d, i) => `<li class="${d.lat != null ? "golink" : ""}" data-go="dm:${i}" title="Show on map">${esc(d.name || d.type)} · ${U.dist(d.distance_km)} from the site</li>`).join("")}</ul>` : ""}
        <div class="note">Source: ${esc(W.source)}.</div></section>`;
    }

    // suggested harvesting sites
    if (R.input.mode === "harvest" && R.harvest_sites) {
      const hs = R.harvest_sites;
      h += `<section class="card"><h2>Other harvesting sites nearby</h2>
        ${hs.length ? `<p class="hint">Small side gullies (catchment 0.1–5 km²) within about ${U.dist(3, 0)}, tested with a ${U.height(hs[0].H)} structure. Click one to select it, then run the screening.</p>
        <ol class="hsites">${hs.map((x, i) => `<li data-i="${i}"><b>Gully ${"ABCDEF"[i]}</b> · ${U.vol(x.volume_mcm)} · catchment ${U.area(x.catchment_km2)} · water : fill ${NUM(x.wer, 0)} (${werMeaning(x.wer)}) · ${U.dist(x.distance_m / 1000)} away</li>`).join("")}</ol>`
        : `<div class="note">No suitable small side gullies were found within about ${U.dist(3, 0)}.</div>`}</section>`;
    }

    // map layers
    if (state.layersList.length) {
      h += `<section class="card"><h2>Maps of the catchment</h2>
        <div class="layerlist">${[{ key: "none", label: "None" }, ...state.layersList].map((l) => `
          <label><input type="radio" name="rlayer" value="${l.key}" ${state.layer === l.key ? "checked" : ""}> ${l.label}</label>`).join("")}</div>
        <div class="field" style="margin-top:8px"><label for="opac">Transparency</label>
        <input type="range" id="opac" min="0.2" max="1" step="0.05" value="${state.opacity}"></div>
        <div class="note">The layer is drawn on the map; its legend appears in the map's corner.</div></section>`;
    }

    // catchment
    h += `<section class="card"><h2>Catchment</h2>${kv([
      ["Area", `${U.area(C.area_km2)}${C.truncated ? " (lower bound)" : ""}`],
      ["Elevation range", `${U.elev(C.elev_min_m)} – ${U.elev(C.elev_max_m)}`],
      ["Mean slope", `${NUM(C.mean_slope_pct)} %`],
      ["Longest flow path", U.dist(C.longest_flow_path_km)],
      ["Terrain grid used", U.length(C.resolution_m)],
    ])}</section>`;

    // hydrology
    if (K) {
      h += `<section class="card"><h2>Climate & water</h2>${kv([
        ["Average rainfall", `${U.depth(K.mean_annual_precip_mm)} / year`],
        ["Potential evaporation", `${U.depth(K.mean_annual_pet_mm)} / year`],
        ["Climate", `${K.climate_class} (aridity ${NUM(K.aridity_index, 2)})`],
        Y && ["Average river inflow", `${U.vol(Y.mean_annual_runoff_mcm)} / year`],
        Y && ["Dry-year inflow (3 years in 4)", `${U.vol(Y.dependable_runoff_mcm)}`],
        Y && ["Storm runoff (harvestable)", `${U.vol(Y.scs_quickflow_mcm)} / year`],
        F && !R.floods && ["100-year flood (indicative)", U.flow(F.peak_m3s)],
      ])}<div class="chart"><canvas id="cMon"></canvas></div><div class="chart"><canvas id="cAnn"></canvas></div>
      <div class="note">Rainfall and evaporation: ${esc(String(R.data_status.Climate || "ERA5").split(" (")[0])}, ${K.years && K.years.length ? `${K.years[0]}–${K.years[K.years.length - 1]}` : ""}.</div></section>`;
      const FT = R.floods;
      if (FT && FT.rows && FT.rows.length) {
        const what = R.input.mode === "dam" ? "dam spillway" : "harvesting structure";
        h += `<section class="card"><h2>Flood peaks (indicative)</h2>
          <table class="ftab"><thead><tr><th>Flood</th><th>Peak flow</th><th>24-h rain</th></tr></thead><tbody>
          ${FT.rows.map((r) => `<tr class="${r.design ? "design" : ""}"><td>1-in-${NUM(r.T, 0)} year${r.extrapolated ? "*" : ""}</td>
            <td>${U.flow(r.peak_m3s)}</td><td>${U.depth(r.rain_24h_mm)}</td></tr>`).join("")}
          </tbody></table>
          <div class="chart"><canvas id="cFlood"></canvas></div>
          <div class="note"><b>Highlighted:</b> usual design flood for a ${what}: ${R.input.mode === "dam"
            ? "1-in-1,000 year for small and medium earth dams; dams whose failure would endanger lives or property downstream are normally designed for the Probable Maximum Flood, which is larger"
            : "1-in-25 year for check dams, bunds and ponds (1-in-50 to 1-in-100 where failure would damage roads, houses or farmland)"}.
            ${FT.rows.some((r) => r.extrapolated) ? `* Extrapolated well beyond the ${FT.n_years} years of rainfall data, so treat as rough.` : ""}
            ${esc(FT.note.split(".")[0])}; gridded rainfall understates local cloudbursts, so real floods may be larger. Confirm with a hydrological study before design.</div></section>`;
      }
    }

    // sediment
    if (S) {
      h += `<section class="card"><h2>Erosion & silt</h2>${kv([
        ["Average soil loss", U.rate(S.mean_soil_loss_t_ha_yr)],
        ["Silt reaching the site", `${U.mass(S.sediment_yield_t_yr)} / year`],
        ["Years to fill half the storage", (S.life_50pct_years || 0) > 500 ? "more than 500" : NUM(S.life_50pct_years, 0)],
      ])}</section>`;
    }

    // land cover
    if (R.landcover_named && R.landcover_named.length) {
      h += `<section class="card"><h2>Land cover</h2>
        <div class="lcbar">${R.landcover_named.map((c) => `<i title="${c.name} ${NUM(c.pct)}%" style="width:${c.pct}%;background:${c.color}"></i>`).join("")}</div>
        <div class="lclegend">${R.landcover_named.filter((c) => c.pct >= 0.5).map((c) => `<span><i style="background:${c.color}"></i>${c.name} ${NUM(c.pct, 0)}%</span>`).join("")}</div>
        ${[[R.landcover_change_recent, "10 m maps"], [LCc, "300 m maps"]].filter(([c]) => c).map(([c, lab]) => `
          <h3>Change ${c.from_year}–${c.to_year} (${lab})</h3>
          ${c.detected ? kv(Object.entries(c.change_pp).filter(([, v]) => Math.abs(v) >= 0.05)
            .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).map(([k, v]) => [k, `<span class="${v >= 0 ? "pos" : "neg"}">${v >= 0 ? "+" : ""}${NUM(v, 1)} points</span>`]))
            : `<div class="note">No change large enough to detect, so it is treated as neutral in the score.</div>`}`).join("")}
      </section>`;
    }

    // context
    h += `<section class="card"><h2>People, environment & hazards</h2>${kv([
      soc && ["Built-up land flooded", U.area(soc.builtup_km2)],
      soc && ["Farmland flooded", U.area(soc.cropland_km2)],
      soc && ["Villages in the reservoir", soc.settlements_flooded && soc.settlements_flooded.length ? esc(soc.settlements_flooded.join(", ")) : "none mapped"],
      O && ["Inside a protected area", O.protected_inside.length ? esc(O.protected_inside.join(", ")) : "No"],
      O && ["Nearest settlements", esc(O.places.slice(0, 3).map((p) => `${p.name || p.type} (${U.dist(p.distance_km)})`).join(", ") || "–")],
      Q && ["Earthquakes M5+ within " + U.dist(100, 0), `${Q.n_m5_100km} since ${Q.since}`],
      R.soil && ["Soil (0–60 cm)", `clay ${NUM(R.soil.clay, 0)}% · sand ${NUM(R.soil.sand, 0)}% · silt ${NUM(R.soil.silt, 0)}%`],
    ])}</section>`;

    h += `<section class="card"><h2>Data used</h2>${kv(Object.entries(R.data_status).map(([k, v]) => [k, `<span class="${String(v).startsWith("unavailable") ? "neg" : ""}">${esc(v)}</span>`]))}
      <div class="note">Screening result from global open data, not a substitute for survey, investigations or design. Full citations are in the PDF report.</div></section>`;

    const keepScroll = $("#panel").scrollTop;
    $("#results").innerHTML = h;
    $("#panel").scrollTop = keepScroll;
    wirePanel();
    drawCharts();
  }

  function wirePanel() {
    const R = state.R;
    $("#dlJson").onclick = () => download(`wssf_${R.id}.json`, JSON.stringify(R, null, 1), "application/json");
    $("#dlGeo").onclick = () => download(`wssf_${R.id}_layers.geojson`, JSON.stringify(layersFC()), "application/geo+json");
    $("#dlPdf").onclick = downloadPdf;
    const ba = $("#backArea");
    if (ba) ba.onclick = (e) => { e.preventDefault(); showAreaResults(true); };
    const sl = $("#hSlider");
    if (sl) {
      sl.oninput = () => {
        state.idx = Number(sl.value); renderMapScenario();
        const s = scen(); $(".hval").innerHTML = `${U.height(s.H)} <small>score ${Math.round(s.assessment.score)} · storage ${U.vol(s.chosen.volume_mcm)}</small>`;
      };
      sl.onchange = () => { renderPanel(); const s2 = $("#hSlider"); if (s2) s2.focus(); };
    }
    const rs = $("#resetH");
    if (rs) rs.onclick = (e) => { e.preventDefault(); state.idx = R.default_scenario; renderMapScenario(); renderPanel(); };
    document.querySelectorAll("[data-go]").forEach((li) => {
      li.onclick = () => (li.classList.contains("sel") ? clearGo() : goTo(li.dataset.go));   // click again = unselect
    });
    document.querySelectorAll(".clearsel").forEach((b) => { b.onclick = (e) => { e.stopPropagation(); clearGo(); }; });
    syncClear();
    document.querySelectorAll(".hsites li").forEach((li) => {
      li.onclick = () => { const x = R.harvest_sites[Number(li.dataset.i)]; setPoint(x.lat, x.lon); map.setView([x.lat, x.lon], 15);
        $("#siteName").value = (R.input.name ? R.input.name + " – " : "") + `gully ${"ABCDEF"[Number(li.dataset.i)]}`; $("#panel").scrollTop = 0; };
    });
    document.querySelectorAll('input[name="rlayer"]').forEach((r) => { r.onchange = () => setRaster(r.value); });
    const op = $("#opac");
    if (op) op.oninput = () => { state.opacity = Number(op.value); if (state.overlay) state.overlay.setOpacity(state.opacity); };
  }

  // ---- show a listed water body / river / dam on the map
  let goMarker = null, goShape = null;
  function clearGo() {
    if (goMarker) { map.removeLayer(goMarker); goMarker = null; }
    if (goShape) { map.removeLayer(goShape); goShape = null; }
    document.querySelectorAll("[data-go].sel").forEach((x) => x.classList.remove("sel"));
    syncClear();
  }
  function clearAllHighlights() {
    clearGo();
    if (typeof placeMarker !== "undefined" && placeMarker) { placeMarker.remove(); placeMarker = null; }
    if (typeof meMarker !== "undefined" && meMarker) { meMarker.remove(); meCircle.remove(); meMarker = meCircle = null; }
    syncClear();
  }
  function syncClear() {
    const any = !!goMarker;
    document.querySelectorAll(".clearsel").forEach((b) => b.classList.toggle("hidden", !any));
    const extra = (typeof placeMarker !== "undefined" && placeMarker) || (typeof meMarker !== "undefined" && meMarker);
    const mc = document.querySelector(".mapclear");
    if (mc) mc.classList.toggle("hidden", !(any || extra));
  }
  // floating map button: clears highlighted items, searched place and "you are here" marker
  const ClearCtl = L.Control.extend({
    options: { position: "topright" },
    onAdd() {
      const b = L.DomUtil.create("button", "mapclear hidden");
      b.innerHTML = "✕ Clear highlights"; b.title = "Remove highlighted water bodies, dams, searched places and your location marker";
      L.DomEvent.disableClickPropagation(b);
      L.DomEvent.on(b, "click", (e) => { L.DomEvent.preventDefault(e); clearAllHighlights(); });
      return b;
    },
  });
  map.addControl(new ClearCtl());
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !state.fsOpen && document.activeElement !== $("#coord") && goMarker) clearGo();
  });
  function goTo(key) {
    const R = state.R, [kind, id] = key.split(":");
    if (goMarker) { map.removeLayer(goMarker); goMarker = null; }
    if (goShape) { map.removeLayer(goShape); goShape = null; }
    document.querySelectorAll("[data-go]").forEach((x) => x.classList.toggle("sel", x.dataset.go === key));
    let lat, lon, html;
    const osmLink = (o) => o ? `<br><a href="https://www.openstreetmap.org/${o}" target="_blank" rel="noopener">View on OpenStreetMap ↗</a>` : "";
    if (kind === "wb") {
      const b = R.water_bodies.bodies.find((x) => String(x.id) === id); if (!b) return;
      lat = b.lat; lon = b.lon;
      html = `<b>${b.type === "permanent" ? "Permanent" : "Seasonal"} water body</b><br>${U.area(b.area_km2)} · water present ${NUM(b.mean_occurrence, 0)}% of the time<br>${U.dist(b.distance_km)} from the site`;
      const fc = (R.layers.water_bodies || {}).features || [];
      const f = fc.filter((x) => String(x.properties.id) === id);
      if (f.length) {
        goShape = L.geoJSON({ type: "FeatureCollection", features: f }, { style: { color: "#f59e0b", weight: 3, fillOpacity: 0.15 } }).addTo(map);
        map.fitBounds(goShape.getBounds(), { padding: [60, 60], maxZoom: 15 });
      }
    } else {
      const list = kind === "nw" ? R.osm.named_water : R.osm.existing_dams;
      const o = list[Number(id)]; if (!o || o.lat == null) return;
      lat = o.lat; lon = o.lon;
      html = `<b>${esc(o.name || o.type)}</b><br>${esc(o.kind || o.type)} · ${U.dist(o.distance_km)} from the site${osmLink(o.osm)}`;
    }
    goMarker = L.circleMarker([lat, lon], { radius: 10, color: "#f59e0b", weight: 3, fillColor: "#fde68a", fillOpacity: 0.7 })
      .addTo(map).bindPopup(html, { autoPan: false });
    if (!goShape) map.setView([lat, lon], Math.max(map.getZoom(), 14), { animate: false });
    goMarker.on("popupclose", () => {});
    setTimeout(() => goMarker && goMarker.openPopup(), 50);
    syncClear();
  }

  async function downloadPdf() {
    const R = state.R, s = scen();
    const btn = $("#dlPdf"); const old = btn.textContent;
    btn.disabled = true; btn.textContent = "Preparing report…";
    try {
      const nm = R.input.name ? `&name=${encodeURIComponent(R.input.name)}` : "";
      const r = await fetch(`/api/results/${R.id}/report.pdf?units=${state.units}${s ? `&h=${s.H}` : ""}${nm}`);
      if (!r.ok) throw new Error(`Report failed (${r.status})`);
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      const slug = (R.input.name || "").replace(/[^A-Za-z0-9]+/g, "_").replace(/^_|_$/g, "");
      a.download = `WSSF_${slug ? slug + "_" : ""}${NUM(R.site.lat, 4)}_${NUM(R.site.lon, 4)}_${s ? s.H + "m" : ""}_${state.units}.pdf`.replace(/,/g, "");
      a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } catch (e) { $("#pdfNote").textContent = e.message; }
    finally { btn.disabled = false; btn.textContent = old; }
  }

  function layersFC() {
    const R = state.R, s = scen(), feats = [];
    const add = (k, v) => { if (!v) return; (v.type === "FeatureCollection" ? v.features : [v]).forEach((f) => feats.push({ ...f, properties: { ...(f.properties || {}), layer: k } })); };
    Object.entries(R.layers || {}).forEach(([k, v]) => add(k, v));
    if (s) { add("reservoir", s.reservoir); add("dam_axis", s.dam_axis); }
    feats.push({ type: "Feature", geometry: { type: "Point", coordinates: [R.site.lon, R.site.lat] },
                 properties: { layer: "site", score: s ? s.assessment.score : R.assessment.score, height_m: s ? s.H : null } });
    return { type: "FeatureCollection", features: feats };
  }
  function download(name, text, type) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type }));
    a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ------------------------------------------------------------------ charts
  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = "#5d6b7a";
  const BLUE = "#2a78d6", ORANGE = "#eb6834";
  function chart(id, cfg) {
    const fs = state.fsMode;
    if (fs && id !== fs) return;
    const el = fs ? document.getElementById("fsCanvas") : document.getElementById(id); if (!el) return;
    cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10 } } } }, cfg.options || {});
    if (fs) {   // full-screen: larger text, denser grid so levels can be read off
      const o = cfg.options;
      o.font = { size: 14 };
      Object.values(o.scales || {}).forEach((sc) => {
        sc.ticks = Object.assign({ font: { size: 13 }, maxTicksLimit: 24 }, sc.ticks || {});
        sc.grid = Object.assign({ color: "#e5e9ee" }, sc.grid || {});
        if (sc.title) sc.title.font = { size: 14, weight: "600" };
      });
      if (o.plugins && o.plugins.title) o.plugins.title.display = false;
      if (o.plugins) o.plugins.tooltip = Object.assign({ bodyFont: { size: 14 }, titleFont: { size: 14 } }, o.plugins.tooltip || {});
      state.fsChart = new Chart(el, cfg);
      $("#fsTitle").textContent = (cfg._title || "");
      return;
    }
    state.charts[id] = new Chart(el, cfg);
    const box = el.parentElement;
    if (box && !box.querySelector(".fsbtn")) {
      const b = document.createElement("button");
      b.className = "fsbtn"; b.title = "Open full screen"; b.setAttribute("aria-label", "Open chart full screen");
      b.innerHTML = "⤢";
      b.addEventListener("click", () => openFull(id));
      box.appendChild(b);
    }
  }
  function apiError(js, r) {
    const d = js && js.detail;
    return typeof d === "string" ? d : (d && d[0] && d[0].msg) || r.statusText || "Request failed";
  }
  // ---- server settings (area limit differs online / on your own computer)
  state.maxAoi = 3000;
  fetch("/api/health").then((r) => r.json()).then((j) => {
    if (j.max_aoi_km2) { state.maxAoi = j.max_aoi_km2; const m = $("#maxAoi"); if (m) m.textContent = NUM(j.max_aoi_km2, 0); }
    if (j.public) { const n = $("#publicNote"); if (n) n.classList.remove("hidden"); }
  }).catch(() => {});

  // ---- About box
  const aboutModal = $("#aboutModal");
  function openAbout(e) {
    if (e) e.preventDefault();
    aboutModal.classList.remove("hidden");
    fetch("/api/health").then((r) => r.json()).then((j) => { $("#aboutVer").textContent = `Analysis version ${j.version}.`; }).catch(() => {});
  }
  function closeAbout() { aboutModal.classList.add("hidden"); }
  $("#aboutLink").addEventListener("click", openAbout);
  $("#aboutClose").addEventListener("click", closeAbout);
  aboutModal.addEventListener("click", (e) => { if (e.target === aboutModal) closeAbout(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !aboutModal.classList.contains("hidden")) closeAbout(); });

  // ---- full-screen chart viewer
  const fsModal = document.createElement("div");
  fsModal.id = "fsModal"; fsModal.className = "fsmodal hidden";
  fsModal.innerHTML = `<div class="fsbox"><div class="fshead"><b id="fsTitle"></b>
      <span class="fshint">Hover anywhere on the chart to read values.</span>
      <button id="fsClose" class="small" aria-label="Close full screen">✕ Close</button></div>
    <div class="fsbody"><canvas id="fsCanvas"></canvas></div></div>`;
  document.body.appendChild(fsModal);
  function openFull(id) {
    state.fsOpen = id;
    fsModal.classList.remove("hidden");
    if (state.fsChart) { state.fsChart.destroy(); state.fsChart = null; }
    state.fsMode = id;
    try { drawCharts(); } finally { state.fsMode = null; }
  }
  function closeFull() {
    fsModal.classList.add("hidden"); state.fsOpen = null;
    if (state.fsChart) { state.fsChart.destroy(); state.fsChart = null; }
  }
  $("#fsClose").addEventListener("click", closeFull);
  fsModal.addEventListener("click", (e) => { if (e.target === fsModal) closeFull(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.fsOpen) closeFull(); });

  function drawCharts() {
    if (!state.fsMode) {
      Object.values(state.charts).forEach((c) => c.destroy()); state.charts = {};
      if (state.fsOpen) setTimeout(() => state.fsOpen && openFull(state.fsOpen), 0);
    }
    const R = state.R, K = R.climate, s = scen();
    const fl = U.fLen(), fv = U.fVol(), fd = U.fDepth();
    if (K) {
      chart("cMon", { _title: "Average month: rainfall and potential evaporation", type: "bar", data: { labels: ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"], datasets: [
        { label: `Rainfall (${U.uDepth()})`, data: K.monthly_precip_mm.map((v) => v * fd), backgroundColor: BLUE, borderRadius: 3 },
        { label: `Potential evaporation (${U.uDepth()})`, data: K.monthly_pet_mm.map((v) => v * fd), type: "line", borderColor: ORANGE, backgroundColor: ORANGE, borderWidth: 2, pointRadius: 3, tension: .3 }] },
        options: { plugins: { title: { display: true, text: "Average month" }, legend: { position: "bottom", labels: { boxWidth: 10 } } } } });
      if (K.years && K.years.length) {
        chart("cAnn", { _title: "Year by year: rainfall and runoff", type: "bar", data: { labels: K.years, datasets: [
          { label: `Rainfall (${U.uDepth()})`, data: K.annual_precip.map((v) => v * fd), backgroundColor: "#9cc7ec" },
          R.yield && { label: `Runoff (${U.uDepth()})`, data: R.yield.annual_runoff_series_mm.map((v) => v * fd), backgroundColor: "#1f5fa8" }].filter(Boolean) },
          options: { plugins: { title: { display: true, text: "Year by year" }, legend: { position: "bottom", labels: { boxWidth: 10 } } } } });
      }
    }
    const FT = R.floods;
    if (FT && FT.rows && FT.rows.length) {
      chart("cFlood", { _title: "Flood peak vs return period", type: "line", data: {
        labels: FT.rows.map((r) => `1-in-${r.T}`),
        datasets: [{ label: `Peak flow (${U.imp ? "cfs" : "m³/s"})`, data: FT.rows.map((r) => r.peak_m3s * (U.imp ? 35.3147 : 1)),
          borderColor: BLUE, backgroundColor: BLUE, borderWidth: 2, tension: .25,
          pointRadius: FT.rows.map((r) => r.design ? 7 : 3), pointBackgroundColor: FT.rows.map((r) => r.design ? "#d62828" : BLUE) }] },
        options: { plugins: { title: { display: true, text: "Flood peak vs return period" }, legend: { display: false },
          tooltip: { callbacks: { label: (c) => { const r = FT.rows[c.dataIndex]; return [`Peak ${U.flow(r.peak_m3s)}`, `24-h rain ${U.depth(r.rain_24h_mm)}`].concat(r.design ? ["Usual design flood"] : []); } } } },
          scales: { x: { title: { display: true, text: "Return period (years)" } }, y: { beginAtZero: true, title: { display: true, text: U.imp ? "cfs" : "m³/s" } } } } });
    }
    const sc = R.scenarios || [];
    if (sc.length) {
      chart("cEAC", { _title: "Dam height vs storage", type: "line", data: { labels: sc.map((x) => NUM(x.H * fl, x.H < 10 ? 1 : 0)), datasets: [
        { label: `Storage (${U.uVol()})`, data: sc.map((x) => x.chosen.volume_mcm * fv), borderColor: BLUE, backgroundColor: BLUE, borderWidth: 2, tension: .2,
          pointRadius: sc.map((_, i) => i === state.idx ? 7 : 2.5), pointBackgroundColor: sc.map((_, i) => i === state.idx ? "#d62828" : BLUE) }] },
        options: { plugins: { title: { display: true, text: "Storage gained at each height" }, legend: { display: false },
          tooltip: { callbacks: { label: (c) => { const x = sc[c.dataIndex]; return [`Storage ${U.vol(x.chosen.volume_mcm)}`, `Area ${U.area(x.chosen.area_km2)}`, `Water : fill ${NUM(x.chosen.wer, 1)} (${werMeaning(x.chosen.wer)})`, `Score ${Math.round(x.assessment.score)}`]; } } } },
          scales: { x: { title: { display: true, text: `Dam height (${U.uLen()})` } }, y: { title: { display: true, text: U.uVol() } } } } });
    }
    const xs = R.cross_section;
    if (xs && s) {
      const x = s.xs, span = (x.right_m - x.left_m);
      const pts = xs.distance_m.map((d, i) => ({ x: d * fl, y: xs.elev_m[i] * fl }));
      const inWin = pts.filter((p) => p.x > (x.left_m - span) * fl && p.x < (x.right_m + span) * fl);
      const bed = R.site && R.site.streambed_elev_m;
      const lev = (v) => `${NUM(v * fl, 0)} ${U.uLen()}`;
      chart("cXS", { _title: `Valley cross-section at the dam, ${U.height(s.H)} ${R.input.mode === "dam" ? "dam" : "structure"}`, type: "line", data: { datasets: [
        { label: "Ground", data: pts, borderColor: "#7a5c3a", backgroundColor: "rgba(122,92,58,.18)", fill: "start", pointRadius: 0, pointHoverRadius: 4, borderWidth: 1.5, tension: .1 },
        { label: `Dam crest ${lev(x.crest_elev)}`, data: [{ x: x.left_m * fl, y: x.crest_elev * fl }, { x: x.right_m * fl, y: x.crest_elev * fl }], borderColor: "#d62828", pointRadius: 0, borderWidth: 2.5 },
        { label: `Full supply level ${lev(x.fsl)}`, data: [{ x: x.left_m * fl, y: x.fsl * fl }, { x: x.right_m * fl, y: x.fsl * fl }], borderColor: BLUE, pointRadius: 0, borderDash: [5, 3], borderWidth: 2 },
        bed != null && { label: `Streambed ${lev(bed)}`, data: [{ x: x.left_m * fl, y: bed * fl }, { x: x.right_m * fl, y: bed * fl }], borderColor: "#6b7280", pointRadius: 0, borderDash: [2, 3], borderWidth: 1.5 }].filter(Boolean) },
        options: { parsing: false, interaction: { mode: "nearest", axis: "x", intersect: false },
          plugins: { tooltip: { callbacks: {
            title: (it) => it.length ? `${NUM(it[0].parsed.x, 0)} ${U.uLen()} across` : "",
            label: (c) => c.datasetIndex === 0 ? `Ground ${NUM(c.parsed.y, 0)} ${U.uLen()}` : c.dataset.label } },
            title: { display: true, text: "Valley cross-section at the dam" }, legend: { position: "bottom", labels: { boxWidth: 10 } } },
          scales: { x: { type: "linear", min: (x.left_m - span) * fl, max: (x.right_m + span) * fl, title: { display: true, text: `Distance across valley (${U.uLen()})` } },
                    y: { min: inWin.length ? Math.min(...inWin.map((p) => p.y)) - 2 * fl : undefined,
                         max: inWin.length ? Math.max(Math.max(...inWin.map((p) => p.y)), x.crest_elev * fl) + 5 * fl : undefined,
                         title: { display: true, text: `Elevation (${U.uLen()})` } } } } });
    }
  }

  // ================================================================== AREA SEARCH
  const areaLayers = {};
  let aoiLayer = null, drawLayer = null, drawGuide = null;

  document.querySelectorAll(".tabs .tab").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  function setTab(t) {
    state.tab = t;
    document.querySelectorAll(".tabs .tab").forEach((x) => x.classList.toggle("on", x.dataset.tab === t));
    $("#tab-site").classList.toggle("hidden", t !== "site");
    $("#tab-area").classList.toggle("hidden", t !== "area");
    if (t === "area") cancelDraw();
  }

  // ---- geodesic area of a lon/lat polygon (km²), spherical approximation
  function ringAreaKm2(coords) {
    const R = 6378.137, rad = Math.PI / 180;
    let a = 0;
    for (let i = 0; i < coords.length - 1; i++) {
      const [x1, y1] = coords[i], [x2, y2] = coords[i + 1];
      a += (x2 - x1) * rad * (2 + Math.sin(y1 * rad) + Math.sin(y2 * rad));
    }
    return Math.abs(a * R * R / 2);
  }
  function geomAreaKm2(g) {
    const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
    return polys.reduce((s, p) => s + ringAreaKm2(p[0]) - p.slice(1).reduce((h, r) => h + ringAreaKm2(r), 0), 0);
  }
  function setAoi(geom, areaKm2) {
    state.aoi = geom;
    if (aoiLayer) map.removeLayer(aoiLayer);
    aoiLayer = L.geoJSON(geom, { style: { color: "#7c3aed", weight: 2.5, dashArray: "6 4", fillOpacity: 0.04 }, interactive: false }).addTo(map);
    map.fitBounds(aoiLayer.getBounds(), { padding: [20, 20] });
    const a = areaKm2 ?? geomAreaKm2(geom);
    const big = a > state.maxAoi;
    $("#aoiInfo").innerHTML = `Area selected: <b>${U.area(a)}</b>${big ? ` <span class="neg">(too large; the maximum is ${NUM(state.maxAoi, 0)} km²)</span>` : ""}`;
    $("#runArea").disabled = big;
  }

  // ---- drawing (polygon: click points, double-click or Finish; rectangle: two corners)
  function startDraw(type) {
    cancelDraw();
    state.draw = { type, pts: [] };
    map.doubleClickZoom.disable();
    map.getContainer().style.cursor = "crosshair";
    $("#drawHelp").classList.remove("hidden");
    $("#drawHelp").innerHTML = type === "poly"
      ? 'Click on the map to add corners. <b>Double-click</b> or <a href="#" id="finishDraw">finish</a> to close the shape. <a href="#" id="cancelDraw">Cancel</a> (Esc).'
      : 'Click the <b>first corner</b>, then the <b>opposite corner</b>. <a href="#" id="cancelDraw">Cancel</a> (Esc).';
    const f = $("#finishDraw"); if (f) f.onclick = (e) => { e.preventDefault(); finishDraw(); };
    $("#cancelDraw").onclick = (e) => { e.preventDefault(); cancelDraw(); };
  }
  function cancelDraw() {
    state.draw = null;
    if (drawLayer) { map.removeLayer(drawLayer); drawLayer = null; }
    if (drawGuide) { map.removeLayer(drawGuide); drawGuide = null; }
    map.doubleClickZoom.enable();
    map.getContainer().style.cursor = "";
    $("#drawHelp").classList.add("hidden");
  }
  function redrawSketch(cursor) {
    const d = state.draw; if (!d) return;
    if (drawLayer) map.removeLayer(drawLayer);
    let pts = d.pts.slice();
    if (d.type === "rect" && pts.length === 1 && cursor) {
      const [a] = pts; pts = [a, [a[0], cursor[1]], cursor, [cursor[0], a[1]]];
      drawLayer = L.polygon(pts, { color: "#7c3aed", weight: 2, dashArray: "4 4", fillOpacity: 0.08, interactive: false }).addTo(map);
      return;
    }
    if (cursor) pts.push(cursor);
    if (pts.length >= 2) drawLayer = L.polygon(pts, { color: "#7c3aed", weight: 2, dashArray: "4 4", fillOpacity: 0.08, interactive: false }).addTo(map);
    else if (pts.length === 1) drawLayer = L.circleMarker(pts[0], { radius: 4, color: "#7c3aed", interactive: false }).addTo(map);
  }
  map.on("click", (e) => {
    const d = state.draw; if (!d) return;
    const p = [e.latlng.lat, e.latlng.lng];
    d.pts.push(p);
    if (d.type === "rect" && d.pts.length === 2) {
      const [a, b] = d.pts; d.pts = [a, [a[0], b[1]], b, [b[0], a[1]]]; finishDraw(); return;
    }
    redrawSketch();
  });
  map.on("mousemove", (e) => { if (state.draw && state.draw.pts.length) redrawSketch([e.latlng.lat, e.latlng.lng]); });
  map.on("dblclick", (e) => { if (state.draw && state.draw.type === "poly") { L.DomEvent.stop(e); finishDraw(); } });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.draw) cancelDraw(); });
  function finishDraw() {
    const d = state.draw; if (!d) return;
    // remove duplicate final point added by the double-click's click events
    const pts = d.pts.filter((p, i, a) => i === 0 || Math.abs(p[0] - a[i - 1][0]) + Math.abs(p[1] - a[i - 1][1]) > 1e-7);
    cancelDraw();
    if (pts.length < 3) { $("#aoiInfo").textContent = "Need at least 3 corners. Please draw again."; return; }
    const ring = pts.map(([la, lo]) => [lo, la]); ring.push(ring[0]);
    setAoi({ type: "Polygon", coordinates: [ring] });
  }
  $("#drawPoly").onclick = () => startDraw("poly");
  $("#drawRect").onclick = () => startDraw("rect");
  $("#clearAoi").onclick = () => {
    cancelDraw(); state.aoi = null; if (aoiLayer) { map.removeLayer(aoiLayer); aoiLayer = null; }
    clearArea(); $("#aoiInfo").textContent = "No area selected"; $("#runArea").disabled = true;
  };
  $("#aoiFile").onchange = async (ev) => {
    const files = [...ev.target.files]; if (!files.length) return;
    const fd = new FormData(); files.forEach((f) => fd.append("files", f, f.name));
    $("#aoiInfo").textContent = "Reading file…"; $("#err").classList.add("hidden");
    try {
      const r = await fetch("/api/aoi/parse", { method: "POST", body: fd });
      const js = await r.json();
      if (!r.ok) throw new Error(js.detail || "Could not read the file");
      setAoi(js.geometry, js.area_km2);
      if (!$("#areaName").value) $("#areaName").value = files[0].name.replace(/\.[^.]+$/, "");
    } catch (e) { $("#aoiInfo").textContent = "No area selected"; $("#err").textContent = e.message; $("#err").classList.remove("hidden"); }
    ev.target.value = "";
  };

  // ---- run area scan
  $("#runArea").onclick = async () => {
    if (!state.aoi) return;
    $("#err").classList.add("hidden"); $("#progress").classList.remove("hidden"); $("#runArea").disabled = true;
    $("#results").innerHTML = ""; state.area = null; clearArea();
    setProgress(1, "Submitting… An area search screens the best sites in full and takes several minutes.");
    try {
      const r = await fetch("/api/area_scan", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ aoi: state.aoi, mode: state.mode }) });
      if (!r.ok) throw new Error(apiError(await r.json().catch(() => ({})), r));
      const { job_id } = await r.json();
      let j;
      while (true) {
        await new Promise((res) => setTimeout(res, 1200));
        j = await (await fetch(`/api/jobs/${job_id}`)).json();
        setProgress(j.progress, j.message);
        if (j.status !== "running") break;
      }
      if (j.status === "error") throw new Error(j.error);
      state.area = await (await fetch(`/api/area_results/${j.result_id}`)).json();
      state.area.name = $("#areaName").value.trim();
      showAreaResults(true);
    } catch (e) { $("#err").textContent = e.message; $("#err").classList.remove("hidden"); }
    finally { $("#progress").classList.add("hidden"); $("#runArea").disabled = false; }
  };

  function clearArea() { clearGroup(areaLayers); Object.keys(areaLayers).forEach((k) => delete areaLayers[k]); }

  function showAreaResults(fit) {
    const A = state.area; if (!A) return;
    // hide single-site layers
    clearGroup(state.base); state.base = {}; clearGroup(state.scen); state.scen = {};
    if (state.overlay) { map.removeLayer(state.overlay); state.overlay = null; state.rasterMeta = null; showLegend(); }
    if (clickMarker) { clickMarker.remove(); clickMarker = null; }
    setTab("area"); state.view = "area";
    if (!state.aoi) setAoi(A.aoi.geometry, A.aoi_area_km2);
    clearArea();
    addLayer(areaLayers, "Streams (area)", A.streams, { color: "#2f8fd8", weight: 1.2, opacity: 0.8 });
    const res = L.layerGroup(), axes = L.layerGroup(), marks = L.layerGroup();
    A.sites.forEach((s) => {
      if (s.reservoir) L.geoJSON(s.reservoir, { style: { color: "#0891b2", weight: 1, fillColor: "#36c6f4", fillOpacity: 0.55 } })
        .bindTooltip(`#${s.rank}: ${U.vol(s.volume_mcm)} at ${U.height(s.H)}`).addTo(res);
      if (s.dam_axis) L.geoJSON(s.dam_axis, { style: { color: "#d62828", weight: 3 } }).addTo(axes);
      const mk = L.marker([s.lat, s.lon], { icon: L.divIcon({ className: "rankmark", iconSize: [26, 26],
        html: `<span style="background:${colorFor(s.score)}">${s.rank}</span>` }) })
        .bindTooltip(`#${s.rank} · score ${Math.round(s.score)} · ${U.vol(s.volume_mcm)}`);
      mk.on("click", () => focusSite(s.rank));
      mk.addTo(marks);
    });
    [["Candidate reservoirs", res], ["Candidate dam axes", axes], ["Candidate sites", marks]].forEach(([n, l]) => {
      l.addTo(map); ctrl.addOverlay(l, n); areaLayers[n] = l; });
    if (fit && aoiLayer) map.fitBounds(aoiLayer.getBounds(), { padding: [20, 20] });
    renderAreaPanel();
  }

  function renderAreaPanel() {
    const A = state.area, isDam = A.mode === "dam";
    const kind = isDam ? "storage-dam" : "water-harvesting";
    let h = `<section class="card">
      ${A.name ? `<div class="sitename" style="margin:0 0 4px">${esc(A.name)}</div>` : ""}
      <h2>Top ${A.sites.length} ${kind} site${A.sites.length === 1 ? "" : "s"}</h2>
      <p class="hint">Scanned ${U.area(A.aoi_area_km2)} · ${A.n_candidates} stream points tested quickly · best ${A.n_full ?? A.sites.length} screened in full
      ${A.climate ? ` · rainfall ${U.depth(A.climate.mean_annual_precip_mm)}/yr (${A.climate.climate_class})` : ""}.</p>
      <div class="note">${esc(A.note)}</div>
      <div class="btnrow" style="margin-top:8px">
        <button class="small" id="dlSitesCsv">Sites (CSV)</button>
        <button class="small" id="dlSitesGeo">Sites (GeoJSON)</button>
      </div></section>`;
    if (!A.sites.length) {
      h += `<section class="card"><div class="answer no">No sites passed the quick test in this area. Try the other scheme type or a larger area.</div></section>`;
    } else {
      h += `<section class="card"><h2>Ranked sites</h2><p class="hint">Click a site to zoom to it. <b>Open full results</b> shows the detailed screening (same score), height slider, maps and PDF report.</p>
        <ol class="ranklist">${A.sites.map((s) => `
          <li id="rk${s.rank}">
            <div class="rkhead"><span class="rkn" style="background:${colorFor(s.score)}">${s.rank}</span>
              <b>${Math.round(s.score)}</b><span class="rkrate">${s.rating}</span></div>
            <div class="rkbody">${U.vol(s.volume_mcm)} with a ${U.height(s.H)} ${isDam ? "dam" : "structure"} · crest ${U.length(s.crest_length_m)} ·
              water : fill ${NUM(s.wer, 0)} (${werMeaning(s.wer)}) · catchment ${U.area(s.catchment_km2)}</div>
            <div class="rkgroups">${(s.groups || []).map((g) => `<span title="${esc(g.label)}: ${g.verdict}"><i style="background:${vColor(g.verdict)}"></i>${esc(g.label.split(/[ ,&]/)[0])}</span>`).join("")}</div>
            ${(s.flags || []).length ? `<div class="rkflags">${s.flags.map((f) => `⚠ ${esc(f)}`).join("<br>")}</div>` : ""}
            <div class="btnrow"><button class="small" data-zoom="${s.rank}">Zoom</button>
              <button class="small small-go" data-open="${s.rank}">Open full results →</button></div>
          </li>`).join("")}</ol></section>`;
    }
    $("#results").innerHTML = h;
    $("#dlSitesCsv").onclick = () => download(`wssf_area_${A.id}.csv`, sitesCsv(), "text/csv");
    $("#dlSitesGeo").onclick = () => download(`wssf_area_${A.id}.geojson`, JSON.stringify(sitesFC()), "application/geo+json");
    document.querySelectorAll("[data-zoom]").forEach((b) => b.onclick = () => focusSite(Number(b.dataset.zoom)));
    document.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => openSite(Number(b.dataset.open)));
    document.querySelectorAll(".ranklist li").forEach((li) => li.addEventListener("click", (e) => {
      if (e.target.tagName !== "BUTTON") focusSite(Number(li.id.slice(2)));
    }));
  }

  function focusSite(rank) {
    const s = state.area.sites.find((x) => x.rank === rank); if (!s) return;
    map.setView([s.lat, s.lon], state.area.mode === "dam" ? 14 : 16);
    document.querySelectorAll(".ranklist li").forEach((li) => li.classList.toggle("sel", li.id === `rk${rank}`));
    const el = $(`#rk${rank}`); if (el) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  function openSite(rank) {
    const A = state.area, s = A.sites.find((x) => x.rank === rank); if (!s) return;
    clearArea();
    setTab("site");
    const name = `${A.name || "Area"} – site #${rank}`;
    $("#siteName").value = name;
    state.mode = A.mode;
    document.querySelectorAll("#modeSeg button").forEach((x) => x.classList.toggle("on", x.dataset.mode === A.mode));
    $("#err").classList.add("hidden");
    // the full result was already computed during the area search: load it as-is (same score)
    showResult(s.result_id, name).then(() => {
      setPoint(state.R.input.lat, state.R.input.lon);
      $("#snap").value = state.R.input.snap_radius_m || "";   // re-running gives the identical cached result
    }).catch((e) => { $("#err").textContent = e.message; $("#err").classList.remove("hidden"); });
  }
  function sitesCsv() {
    const A = state.area;
    const hdr = ["rank", "score", "rating", "lat", "lon", `height_${U.uLen()}`, `storage_${U.imp ? "acre_ft" : "mcm"}`,
                 `lake_area_${U.imp ? "acres" : "km2"}`, `crest_length_${U.uLen()}`, "water_to_fill_ratio", `catchment_${U.imp ? "mi2" : "km2"}`];
    const rows = A.sites.map((s) => [s.rank, s.score, s.rating, s.lat.toFixed(6), s.lon.toFixed(6), (s.H * U.fLen()).toFixed(1),
      (s.volume_mcm * U.fVol()).toFixed(3), (U.imp ? s.area_km2 * 247.105 : s.area_km2).toFixed(3),
      (s.crest_length_m * U.fLen()).toFixed(0), s.wer.toFixed(1), (U.imp ? s.catchment_km2 * 0.386102 : s.catchment_km2).toFixed(3)]);
    return [hdr, ...rows].map((r) => r.join(",")).join("\n");
  }
  function sitesFC() {
    const A = state.area, feats = [A.aoi];
    A.sites.forEach((s) => {
      const p = { rank: s.rank, score: s.score, rating: s.rating, height_m: s.H, storage_mcm: s.volume_mcm, catchment_km2: s.catchment_km2, wer: s.wer };
      feats.push({ type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] }, properties: { ...p, layer: "site" } });
      if (s.reservoir) feats.push({ ...s.reservoir, properties: { ...p, layer: "reservoir" } });
      if (s.dam_axis) feats.push({ ...s.dam_axis, properties: { ...p, layer: "dam_axis" } });
    });
    return { type: "FeatureCollection", features: feats };
  }
})();
