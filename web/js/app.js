/* NETRA — Risk Map Dashboard (M11)
 * Owner: E. Builds against fixtures per the Section 6 fixture-first rule.
 * Swap-in point for the live API is isolated to loadData() below —
 * when D's endpoints (Section 5.5) are up, this file does not need to change,
 * it already tries them first and only falls back to fixtures on failure.
 */

(() => {
  "use strict";

  const RISK_COLORS = { low: "#3ba55d", med: "#c7d84a", high: "#e2812c", severe: "#e5484d" };
  const SEV_COLORS = { severe: "#e5484d", conflict: "#f5b83d" };

  const state = {
    online: false,
    segments: [],
    events: [],
    narratives: {},
    norms: null,
    health: null,
    clips: {},
    filters: { light: "any", weather: "any", severity: "any" },
    selectedSegment: null,
    map: null,
    roads: [],
    roadSource: null,
    segmentLayers: {},
    casingLayers: {},
    eventLayers: [],
    hourChart: null,
    speedChart: null,
    apiState: "offline",
    activeTileLayer: null,
    tilesFailed: false,
  };

  // ---------- 1. Data loading: live API first, fixtures on any failure ----------
  // Matches acceptance criteria for M11: "No blank screen on API failure —
  // degrade to cached data with a visible 'offline' badge."

  function apiUrl(path) {
    const base = (window.NETRA_CONFIG && window.NETRA_CONFIG.apiBase) || "";
    return base.replace(/\/$/, "") + path;
  }

  async function tryFetchJSON(path, timeoutMs = 1200) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(apiUrl(path), { signal: ctrl.signal });
      clearTimeout(t);
      if (!res.ok) throw new Error("bad status " + res.status);
      const envelope = await res.json();
      if (!envelope || envelope.ok !== true) throw new Error("envelope not ok");
      return envelope.data;
    } catch (e) {
      clearTimeout(t);
      return null;
    }
  }

  /** Build the query string D's /api/events accepts (Section 5.5). */
  function eventsQuery() {
    const f = state.filters;
    const p = new URLSearchParams();
    if (f.light !== "any") p.set("light", f.light);
    if (f.weather !== "any") p.set("weather", f.weather);
    const q = p.toString();
    return q ? `/api/events?${q}` : "/api/events";
  }

  async function loadData() {
    const [liveSegments, liveEvents, liveHealth] = await Promise.all([
      tryFetchJSON("/api/segments"),
      tryFetchJSON(eventsQuery()),
      tryFetchJSON("/api/health"),
    ]);

    const fx = window.NETRA_FIXTURES || {};

    state.norms = fx.norms || null;
    state.narratives = fx.narratives || {};
    state.clips = fx.clips_b64 || {};

    if (liveEvents) {
      state.online = true;
      state.apiState = "live";
      state.events = liveEvents;
      state.health = liveHealth || null;
      state.serverSegments = liveSegments || null;
    } else {
      state.online = false;
      state.events = fx.events || [];
      state.serverSegments = null;
      // The server answering /api/health while /api/events fails means the
      // API is up but its database is not. That is a very different problem
      // from an unreachable server, and saying "offline" for both sends you
      // looking in the wrong place.
      if (liveHealth) {
        state.apiState = "degraded";
        state.health = liveHealth;
      } else {
        state.apiState = "offline";
        state.health = (fx.health && fx.health.data) || null;
      }
    }

    // Real street geometry. Corridors come from OSM because the server's
    // /api/segments returns scored points, not centrelines — see
    // applyServerRisk() for how the two are reconciled.
    const network = await window.NetraRoads.load();
    if (network) {
      state.roads = network.roads;
      state.roadSource = network.source;
      state.segments = window.NetraRoads.selectCorridors(network.roads);
      state.events = window.NetraRoads.snapEventsToCorridors(state.events, state.segments);
    } else {
      state.roads = [];
      state.roadSource = null;
      state.segments = [];
    }
  }

  /**
   * The server clusters events onto a 0.01-degree grid (~1.1 km) and returns
   * a point per cluster with no geometry, so its segments cannot be drawn as
   * roads and are far too coarse to distinguish arms of one junction.
   *
   * Per-corridor risk is therefore computed here from the full event list,
   * which /api/events returns at full fidelity. The server's own score is
   * kept for display so the two can be compared rather than silently diverge.
   */
  function serverRiskSummary() {
    const segs = state.serverSegments;
    if (!Array.isArray(segs) || !segs.length) return null;
    // risk_score is AVG(severe ? 2 : 1), so it lives in 1.0-2.0.
    const scores = segs.map((s) => s.risk_score).filter((v) => typeof v === "number");
    if (!scores.length) return null;
    const peak = Math.max(...scores);
    return {
      clusters: segs.length,
      peak: peak,
      peakPct: Math.round(Math.min(100, Math.max(0, (peak - 1) * 100))),
      conflicts24h: segs.reduce((t, s) => t + (s.conflict_count_24h || 0), 0),
    };
  }

  function stripUnderscoreFields(ev) {
    const clean = {};
    for (const k of Object.keys(ev)) if (!k.startsWith("_")) clean[k] = ev[k];
    return clean;
  }

  // ---------- 2. Filtering ----------

  function eventPasses(ev) {
    const f = state.filters;
    if (f.light !== "any" && ev.conditions?.light !== f.light) return false;
    if (f.weather !== "any" && ev.conditions?.weather !== f.weather) return false;
    if (f.severity !== "any" && ev.severity !== f.severity) return false;
    return true;
  }

  function filteredEvents() {
    return state.events.filter(eventPasses);
  }

  function segmentIdForEvent(ev) {
    if (ev._corridor_id) return ev._corridor_id;
    // Live-API events won't carry the snap helper, so fall back to the
    // nearest monitored corridor by true ground distance.
    let best = null, bestD = Infinity;
    for (const seg of state.segments) {
      for (const path of seg.paths || [seg.path || []]) {
        const d = window.NetraRoads.minDistToPathM(ev.location, path);
        if (d < bestD) { bestD = d; best = seg.id; }
      }
    }
    return best;
  }

  function riskBucket(score) {
    if (score >= 75) return "severe";
    if (score >= 50) return "high";
    if (score >= 25) return "med";
    return "low";
  }

  function computeSegmentStats(segId) {
    const evs = filteredEvents().filter((e) => segmentIdForEvent(e) === segId);
    const hourly = new Array(24).fill(0);
    const speeds = [];
    let severe = 0;
    for (const e of evs) {
      const h = parseInt((e.time || "T00").split("T")[1]?.slice(0, 2) || "0", 10);
      hourly[h] += 1;
      speeds.push(e.vehicle_a.speed_kmh, e.vehicle_b.speed_kmh);
      if (e.severity === "severe") severe += 1;
    }
    const risk = Math.min(100, evs.length * 9 + severe * 12);
    return { count: evs.length, severe, hourly, speeds, risk };
  }

  // ---------- 3. Map ----------

  function initMap() {
    const CFG = window.NETRA_CONFIG;
    state.map = L.map("map", {
      zoomControl: true,
      attributionControl: false,
      minZoom: 12,
      maxZoom: 20,
    }).setView(CFG.junction.center, 16);

    L.control.attribution({ prefix: false }).addTo(state.map);

    // Basemap goes down first so our layers sit above it.
    addTileLayer();

    drawContextRoads();
    drawSegments();
    drawEventMarkers();

    const allPoints = state.segments.flatMap((s) =>
      (s.paths || [s.path || []]).flat());
    if (allPoints.length) {
      state.map.fitBounds(L.latLngBounds(allPoints), { padding: [70, 70] });
    }

    state.map.on("click", () => selectSegment(null));
    updateMapNote();
  }

  function cartoTileUrl() {
    const CFG = window.NETRA_CONFIG;
    const base = `https://{s}.basemaps.cartocdn.com/${CFG.carto.style}/{z}/{x}/{y}{r}.png`;
    return CFG.carto.key ? `${base}?key=${encodeURIComponent(CFG.carto.key)}` : base;
  }

  function addTileLayer() {
    if (state.activeTileLayer) {
      state.map.removeLayer(state.activeTileLayer);
      state.activeTileLayer = null;
    }
    let tileErrors = 0;
    const layer = L.tileLayer(cartoTileUrl(), {
      subdomains: "abcd",
      maxZoom: 20,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    });
    layer.on("tileerror", () => {
      tileErrors += 1;
      if (tileErrors === 6) {
        state.tilesFailed = true;
        updateMapNote();
      }
    });
    layer.on("load", () => {
      state.tilesFailed = false;
      updateMapNote();
    });
    layer.addTo(state.map);
    layer.bringToBack();
    state.activeTileLayer = layer;
  }

  /** Context roads: every other OSM way in the box, drawn dim and inert. */
  function drawContextRoads() {
    const corridorIds = new Set(state.segments.flatMap((s) => s.memberIds || [s.id]));
    for (const road of state.roads) {
      if (corridorIds.has(road.id)) continue;
      L.polyline(road.path, {
        color: "#4a5a6b",
        weight: window.NetraRoads.ROAD_WEIGHTS[road.highway] || 1.2,
        opacity: 0.5,
        interactive: false,
      }).addTo(state.map);
    }
  }

  function updateMapNote() {
    const note = document.getElementById("mapModeNote");
    const bits = [];
    if (state.tilesFailed) {
      bits.push("Basemap tiles unreachable — road and event layers unaffected.");
    }
    if (!state.roads.length) {
      bits.push("No street geometry loaded. Run tools/fetch_roads.py, or connect to the internet once to fetch it.");
    } else if (state.roadSource === "overpass") {
      bits.push("Street geometry fetched live from OpenStreetMap — bake it to fixtures before the demo.");
    }
    if (!bits.length) {
      note.classList.add("hidden");
      return;
    }
    note.textContent = bits.join(" ");
    note.classList.remove("hidden");
  }

  function drawSegments() {
    for (const seg of state.segments) {
      const stats = computeSegmentStats(seg.id);
      const bucket = riskBucket(stats.risk);
      // A wide dark casing under each corridor makes the risk colour read
      // clearly against a busy basemap.
      const geom = seg.paths || [seg.path];
      const casing = L.polyline(geom, {
        color: "#0b1017",
        weight: 9,
        opacity: 0.85,
        lineCap: "round",
        interactive: false,
      }).addTo(state.map);
      const line = L.polyline(geom, {
        color: RISK_COLORS[bucket],
        weight: 5,
        opacity: 1,
        lineCap: "round",
      }).addTo(state.map);
      line.on("click", (e) => {
        L.DomEvent.stopPropagation(e);
        selectSegment(seg.id);
      });
      line.bindTooltip(`${seg.name} — ${stats.count} conflicts`, { sticky: true });
      state.segmentLayers[seg.id] = line;
      state.casingLayers[seg.id] = casing;
    }
  }

  function redrawSegmentColors() {
    for (const seg of state.segments) {
      const stats = computeSegmentStats(seg.id);
      const bucket = riskBucket(stats.risk);
      const line = state.segmentLayers[seg.id];
      if (line) {
        line.setStyle({ color: RISK_COLORS[bucket] });
        line.setTooltipContent(`${seg.name} — ${stats.count} conflicts`);
      }
    }
  }

  function drawEventMarkers() {
    for (const layer of state.eventLayers) state.map.removeLayer(layer);
    state.eventLayers = [];

    for (const ev of filteredEvents()) {
      const color = SEV_COLORS[ev.severity] || "#8fa0b5";
      const size = ev.severity === "severe" ? 20 : 14;
      const icon = L.divIcon({
        className: "hotspot-icon",
        html: `<span class="hotspot-dot${ev.severity === "severe" ? " severe" : ""}" style="--dot-color:${color}; width:${size}px; height:${size}px;"></span>`,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
      });
      const marker = L.marker(ev.location, { icon }).addTo(state.map);
      marker.on("click", (e) => {
        L.DomEvent.stopPropagation(e);
        openEventModal(ev);
      });
      marker.bindTooltip(`${ev.event_id} · TTC ${ev.ttc_s}s`);
      state.eventLayers.push(marker);
    }
  }

  // ---------- 4. Detail pane (segment click) ----------

  function selectSegment(segId) {
    state.selectedSegment = segId;
    const empty = document.getElementById("detailEmpty");
    const content = document.getElementById("detailContent");

    if (!segId) {
      empty.classList.remove("hidden");
      content.classList.add("hidden");
      renderOverview();
      return;
    }

    const seg = state.segments.find((s) => s.id === segId);
    const stats = computeSegmentStats(segId);

    empty.classList.add("hidden");
    content.classList.remove("hidden");
    document.getElementById("detailTitle").textContent = seg.name;
    document.getElementById("detailConflicts").textContent = stats.count;
    document.getElementById("detailSevere").textContent = stats.severe;
    document.getElementById("detailRisk").textContent = stats.risk;

    renderHourChart(stats.hourly);
    renderSpeedChart(stats.speeds);
  }

  /** 0 -> "12AM", 13 -> "1PM". Compact enough for a narrow axis. */
  function hourLabel(h) {
    const suffix = h < 12 ? "AM" : "PM";
    const h12 = h % 12 === 0 ? 12 : h % 12;
    return `${h12}${suffix}`;
  }

  function renderHourChart(hourly) {
    const ctx = document.getElementById("hourChart");
    if (state.hourChart) state.hourChart.destroy();
    state.hourChart = new Chart(ctx, {
      type: "bar",
      data: {
        // Label every third hour so the axis stays legible in the side panel;
        // the tooltip still names the exact hour for every bar.
        labels: hourly.map((_, h) => (h % 3 === 0 ? hourLabel(h) : "")),
        datasets: [{ data: hourly, backgroundColor: "#4fb0ff", borderRadius: 3, barThickness: 6 }],
      },
      options: {
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => {
                const h = items[0].dataIndex;
                return `${hourLabel(h)} – ${hourLabel((h + 1) % 24)}`;
              },
              label: (item) => {
                const n = item.parsed.y;
                return `${n} conflict${n === 1 ? "" : "s"}`;
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: "#8fa0b5", font: { size: 10 }, maxRotation: 0, autoSkip: false } },
          y: { beginAtZero: true, ticks: { color: "#8fa0b5", stepSize: 1, precision: 0 }, grid: { color: "#223144" } },
        },
      },
    });
  }

  function renderSpeedChart(speeds) {
    const ctx = document.getElementById("speedChart");
    const buckets = new Array(10).fill(0); // 0-100 km/h in steps of 10
    for (const s of speeds) buckets[Math.min(9, Math.floor(s / 10))] += 1;
    const p85 = state.norms?.speed_85_kmh ?? null;

    if (state.speedChart) state.speedChart.destroy();
    state.speedChart = new Chart(ctx, {
      type: "bar",
      data: {
        labels: buckets.map((_, i) => `${i * 10}`),
        datasets: [{ data: buckets, backgroundColor: "#f5b83d", borderRadius: 3 }],
      },
      options: {
        plugins: {
          legend: { display: false },
          annotation: undefined,
        },
        scales: {
          x: { title: { display: true, text: "km/h", color: "#8fa0b5" }, grid: { display: false }, ticks: { color: "#8fa0b5", font: { size: 10 } } },
          y: { beginAtZero: true, ticks: { color: "#8fa0b5", precision: 0 }, grid: { color: "#223144" } },
        },
      },
      plugins: [
        {
          id: "p85line",
          afterDraw(chart) {
            if (p85 == null) return;
            const xScale = chart.scales.x;
            const x = xScale.getPixelForValue(Math.min(9, p85 / 10));
            const { top, bottom, right, left } = chart.chartArea;
            const c = chart.ctx;
            c.save();
            c.strokeStyle = "#e5484d";
            c.setLineDash([5, 4]);
            c.lineWidth = 2;
            c.beginPath();
            c.moveTo(x, top);
            c.lineTo(x, bottom);
            c.stroke();
            c.setLineDash([]);

            // Keep the label inside the plot area: flip it to the left of
            // the line when there isn't room on the right.
            const text = `85th pct ${p85} km/h`;
            c.font = "10px ui-monospace, monospace";
            c.fillStyle = "#e5484d";
            const w = c.measureText(text).width;
            const pad = 5;
            let tx = x + pad;
            if (tx + w > right) tx = x - pad - w;
            if (tx < left) tx = left + 2;
            c.fillText(text, tx, top + 10);
            c.restore();
          },
        },
      ],
    });
  }

  // ---------- 5. Events list ----------

  const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  function formatEventTime(iso) {
    // "2026-08-28T19:47:12" -> "Fri 7:47 pm"
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso || "");
    if (!m) return iso || "—";
    const [, y, mo, d, hh, mm] = m;
    const day = WEEKDAYS[new Date(+y, +mo - 1, +d).getDay()];
    const h = +hh;
    const h12 = h % 12 === 0 ? 12 : h % 12;
    return `${day} ${h12}:${mm} ${h < 12 ? "am" : "pm"}`;
  }

  function renderEventsList() {
    const list = document.getElementById("eventsList");
    const evs = [...filteredEvents()].sort((a, b) => (a.time < b.time ? 1 : -1));
    document.getElementById("eventsCount").textContent = `(${evs.length})`;

    if (evs.length === 0) {
      list.innerHTML = `<p class="empty-note">No conflicts match the current filters.</p>`;
      return;
    }

    list.innerHTML = evs
      .map((ev) => {
        const clipBadge = ev.clip
          ? `<span class="event-clip has-clip">▶ Clip</span>`
          : `<span class="event-clip"></span>`;
        return `
        <div class="event-row" data-id="${ev.event_id}" role="button" tabindex="0">
          <span class="sev-dot ${ev.severity}" title="${ev.severity}"></span>
          <span class="event-id">${ev.event_id}</span>
          <span class="event-time">${formatEventTime(ev.time)}</span>
          <span class="event-type">${ev.type}</span>
          <span class="event-vehicles">${ev.vehicle_a.type} → ${ev.vehicle_b.type}</span>
          <span class="event-cond">
            <span class="pill">${ev.conditions.light}</span>
            <span class="pill">${ev.conditions.weather}</span>
          </span>
          <span class="event-ttc"><em>TTC</em> ${ev.ttc_s.toFixed(2)}s</span>
          ${clipBadge}
        </div>`;
      })
      .join("");

    list.querySelectorAll(".event-row").forEach((row) => {
      const open = () => {
        const ev = state.events.find((e) => e.event_id === row.dataset.id);
        if (ev) openEventModal(ev);
      };
      row.addEventListener("click", open);
      row.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
      });
    });
  }

  // ---------- 6. Event modal: narrative + clip ----------

  function openEventModal(ev) {
    const modal = document.getElementById("clipModal");
    document.getElementById("clipModalTitle").textContent = `${ev.event_id} — ${ev.severity} ${ev.type}`;

    const narrative = state.narratives[ev.event_id];
    // If the server is up, fetch M8's write-up for this event and swap it in
    // when it arrives. The cached copy renders immediately either way, so the
    // modal is never blocked on the network (PRD: cached output cannot fail
    // on stage).
    if (state.online) fetchNarrative(ev.event_id);
    const clipKey = ev.clip ? ev.clip.split("/").pop().replace(/\.(mp4|webm)$/, "") : null;
    const clipB64 = clipKey ? state.clips[clipKey] : null;

    const videoBlock = clipB64
      ? `<video controls preload="metadata" src="data:video/webm;base64,${clipB64}"></video>`
      : `<div class="no-clip">No clip attached to this event yet — the edge pipeline (M2/M3) stores only the ~300‑byte record. Clip retrieval is a future enhancement, not required for the demo.</div>`;

    const narrativeBlock = narrative
      ? `<div class="narrative">${escapeHtml(narrative)}</div>`
      : `<p class="muted small" style="margin-top:10px;">No pre-generated narrative for this event (M8 output not cached for it).</p>`;

    document.getElementById("clipModalBody").innerHTML = `
      ${videoBlock}
      ${narrativeBlock}
      <div class="fields">
        <div><span>Time</span><br>${ev.time}</div>
        <div><span>TTC / PET</span><br>${ev.ttc_s}s / ${ev.pet_s ?? "—"}s</div>
        <div><span>Vehicle A</span><br>${ev.vehicle_a.type}, ${ev.vehicle_a.speed_kmh} km/h</div>
        <div><span>Vehicle B</span><br>${ev.vehicle_b.type}, ${ev.vehicle_b.speed_kmh} km/h${ev.vehicle_b.direction === "against flow" ? " (against flow)" : ""}</div>
        <div><span>Conditions</span><br>${ev.conditions.light}, ${ev.conditions.weather}, ${ev.conditions.surface}</div>
        <div><span>Detection quality</span><br>${ev.detection_quality ?? "—"}</div>
      </div>
    `;
    modal.classList.remove("hidden");
  }

  async function fetchNarrative(eventId) {
    const data = await tryFetchJSON(`/api/events/${encodeURIComponent(eventId)}/narrative`, 2500);
    const text = data && (data.narrative || data.text);
    if (!text) return;
    state.narratives[eventId] = text;
    // Only update if this event's modal is still the one on screen.
    const title = document.getElementById("clipModalTitle").textContent || "";
    if (!title.startsWith(eventId)) return;
    const holder = document.querySelector("#clipModalBody .narrative");
    if (holder) holder.textContent = text;
  }

  function closeModal() {
    document.getElementById("clipModal").classList.add("hidden");
    document.getElementById("clipModalBody").innerHTML = "";
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function renderOverview() {
    const evs = filteredEvents();
    const cfg = window.NETRA_CONFIG;

    document.getElementById("overviewTitle").textContent = cfg.junction.name;
    const [lat, lng] = cfg.junction.center;
    document.getElementById("overviewSub").textContent =
      `${lat.toFixed(5)}, ${lng.toFixed(5)} · ${state.roads.length} roads mapped`;

    document.getElementById("ovConflicts").textContent = evs.length;
    document.getElementById("ovSevere").textContent =
      evs.filter((e) => e.severity === "severe").length;
    document.getElementById("ovCorridors").textContent = state.segments.length;

    const ranked = state.segments
      .map((seg) => ({ seg, stats: computeSegmentStats(seg.id) }))
      .sort((a, b) => b.stats.risk - a.stats.risk);

    const ul = document.getElementById("ovRanking");
    if (!ranked.length) {
      ul.innerHTML = `<li class="empty-note">No corridors loaded.</li>`;
      return;
    }

    ul.innerHTML = ranked
      .map(({ seg, stats }) => {
        const bucket = riskBucket(stats.risk);
        return `
        <li class="rank-row" data-id="${seg.id}" role="button" tabindex="0">
          <span class="rank-swatch" style="background:${RISK_COLORS[bucket]}"></span>
          <span class="rank-name" title="${seg.name}">${seg.name}</span>
          <span class="rank-count">${stats.count}</span>
          <span class="rank-bar"><i style="width:${stats.risk}%;background:${RISK_COLORS[bucket]}"></i></span>
        </li>`;
      })
      .join("");

    ul.querySelectorAll(".rank-row").forEach((row) => {
      const go = () => selectSegment(row.dataset.id);
      row.addEventListener("click", go);
      row.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
      });
      row.addEventListener("mouseenter", () => highlightSegment(row.dataset.id, true));
      row.addEventListener("mouseleave", () => highlightSegment(row.dataset.id, false));
    });
  }

  /** Thicken a corridor when its row in the ranking is hovered. */
  function highlightSegment(segId, on) {
    const line = state.segmentLayers[segId];
    if (line) line.setStyle({ weight: on ? 8 : 5 });
  }

  // ---------- 7. Summary + health ----------

  function renderSummary() {
    const evs = filteredEvents();
    document.getElementById("sumConflicts").textContent = evs.length;
    document.getElementById("sumSevere").textContent = evs.filter((e) => e.severity === "severe").length;
    const dq = evs.map((e) => e.detection_quality).filter((v) => v != null);
    document.getElementById("sumQuality").textContent = dq.length ? (dq.reduce((a, b) => a + b, 0) / dq.length).toFixed(2) : "—";
  }

  function renderHealth() {
    const body = document.getElementById("healthBody");
    const h = state.health;
    if (!h) {
      body.textContent = "No response from /api/health.";
      return;
    }

    const rows = [];

    // D's server reports {postgres, buffer, timestamp}. The fixture uses the
    // older placeholder shape, so handle both rather than showing blanks.
    if ("postgres" in h) {
      rows.push(`Postgres: <b class="${h.postgres ? "ok" : "bad"}">${h.postgres ? "connected" : "down"}</b>`);
      rows.push(`Buffered events: ${h.buffer ?? 0}`);
      if (h.timestamp) rows.push(`Checked: ${String(h.timestamp).slice(11, 19)} UTC`);
    } else {
      rows.push(`Status: ${h.pipeline_status ?? "unknown"}`);
      rows.push(`Frame rate: ${h.frame_rate_fps != null ? h.frame_rate_fps + " fps" : "— (no edge)"}`);
    }

    const srv = serverRiskSummary();
    if (srv) {
      rows.push(`<span class="hr"></span>Server clusters: ${srv.clusters} · ${srv.conflicts24h} conflicts/24h`);
    }

    body.innerHTML = rows.join("<br>");
  }

  function renderStatusBadge() {
    const badge = document.getElementById("statusBadge");
    const label = badge.querySelector(".status-label");

    if (state.apiState === "live") {
      badge.className = "status-badge online";
      label.textContent = "Live API connected";
      badge.title = "Events are coming from the server.";
    } else if (state.apiState === "degraded") {
      badge.className = "status-badge degraded";
      label.textContent = "API up, database down";
      badge.title =
        "The server is reachable but cannot reach Postgres, so it has no events " +
        "to return. Showing fixtures. Check that Postgres is running on port 5432 " +
        "and that the netra database and netra_user login exist.";
    } else {
      badge.className = "status-badge offline";
      label.textContent = "Offline — showing fixtures";
      badge.title =
        "No response from " + ((window.NETRA_CONFIG && window.NETRA_CONFIG.apiBase) || "the API") +
        ". Check the server is running and apiBase in web/js/config.js is correct.";
    }
  }

  // ---------- 7b. Bake road geometry to a fixture ----------

  /**
   * Pulls the street network from Overpass and downloads it as
   * fixtures/road_network.json. Do this once, commit the file, and the
   * demo never touches Overpass again.
   */
  async function bakeRoads() {
    const btn = document.getElementById("bakeRoads");
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Fetching roads…";

    const CFG = window.NETRA_CONFIG;
    const network = await window.NetraRoads.loadLive(CFG.junction.center, CFG.bboxRadiusM);

    if (!network) {
      btn.textContent = "Fetch failed — retry";
      btn.disabled = false;
      setTimeout(() => { btn.textContent = original; }, 4000);
      return;
    }

    const payload = {
      generated: new Date().toISOString(),
      junction: CFG.junction,
      bbox: window.NetraRoads.bboxAround(CFG.junction.center, CFG.bboxRadiusM),
      source: "OpenStreetMap via Overpass API, ODbL",
      roads: network.roads,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "road_network.json";
    a.click();
    URL.revokeObjectURL(url);

    btn.textContent = `Saved ${network.roads.length} roads`;
    setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 4000);
  }

  // ---------- 8. Filters wiring ----------

  function wireFilterGroup(id, key) {
    const group = document.getElementById(id);
    group.querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        group.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");
        state.filters[key] = chip.dataset.value;
        onFiltersChanged();
      });
    });
  }

  async function onFiltersChanged() {
    if (state.online) {
      const evs = await tryFetchJSON(eventsQuery());
      if (evs) {
        state.events = window.NetraRoads.snapEventsToCorridors(evs, state.segments);
      }
    }
    drawEventMarkers();
    redrawSegmentColors();
    renderEventsList();
    renderSummary();
    if (state.selectedSegment) selectSegment(state.selectedSegment);
    else renderOverview();
  }

  function resetFilters() {
    state.filters = { light: "any", weather: "any", severity: "any" };
    document.querySelectorAll(".chip-row").forEach((row) => {
      row.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c.dataset.value === "any"));
    });
    onFiltersChanged();
  }

  // ---------- 9. Boot ----------

  async function boot() {
    document.getElementById("junctionName").textContent = window.NETRA_CONFIG.junction.name;
    await loadData();
    renderStatusBadge();
    initMap();
    renderEventsList();
    renderSummary();
    renderOverview();
    renderHealth();

    wireFilterGroup("filterLight", "light");
    wireFilterGroup("filterWeather", "weather");
    wireFilterGroup("filterSeverity", "severity");
    document.getElementById("resetFilters").addEventListener("click", resetFilters);
    document.getElementById("detailBack").addEventListener("click", () => selectSegment(null));
    document.getElementById("bakeRoads").addEventListener("click", bakeRoads);
    document.getElementById("clipModalClose").addEventListener("click", closeModal);
    document.getElementById("clipModal").addEventListener("click", (e) => {
      if (e.target.id === "clipModal") closeModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeModal();
    });
  }

  boot();
})();
