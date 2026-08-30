/* Real street geometry for the NETRA risk map.
 *
 * Order of preference:
 *   1. fixtures/road_network.json  — baked, offline, what the demo should use
 *   2. live Overpass fetch          — first run / development convenience
 *   3. null                         — caller falls back to a bare marker map
 *
 * Bake step 2 into step 1 with tools/fetch_roads.py (or the "Bake roads"
 * button in the UI) so the demo never depends on Overpass being up.
 */
window.NetraRoads = (() => {
  "use strict";

  const CFG = window.NETRA_CONFIG;

  // Road classes we care about, coarsest first. Used for draw weight too.
  const ROAD_WEIGHTS = {
    trunk: 3.2, primary: 3.0, secondary: 2.4,
    tertiary: 2.0, unclassified: 1.4, residential: 1.4, service: 1.0,
  };

  // ---------- geometry helpers (equirectangular approx — fine at this scale) ----------

  function metresPerDegLat() { return 110574; }
  function metresPerDegLng(lat) { return 111320 * Math.cos((lat * Math.PI) / 180); }

  function distM(a, b) {
    const dLat = (a[0] - b[0]) * metresPerDegLat();
    const dLng = (a[1] - b[1]) * metresPerDegLng((a[0] + b[0]) / 2);
    return Math.hypot(dLat, dLng);
  }

  function pathLengthM(path) {
    let t = 0;
    for (let i = 1; i < path.length; i++) t += distM(path[i - 1], path[i]);
    return t;
  }

  function minDistToPathM(point, path) {
    let best = Infinity;
    for (const p of path) best = Math.min(best, distM(point, p));
    return best;
  }

  /** Point at a given fraction (0..1) along a polyline, by true arc length. */
  function pointAlongPath(path, frac) {
    if (path.length === 1) return path[0].slice();
    const total = pathLengthM(path);
    if (total === 0) return path[0].slice();
    let target = Math.max(0, Math.min(1, frac)) * total;
    for (let i = 1; i < path.length; i++) {
      const seg = distM(path[i - 1], path[i]);
      if (target <= seg || i === path.length - 1) {
        const t = seg === 0 ? 0 : target / seg;
        return [
          path[i - 1][0] + (path[i][0] - path[i - 1][0]) * t,
          path[i - 1][1] + (path[i][1] - path[i - 1][1]) * t,
        ];
      }
      target -= seg;
    }
    return path[path.length - 1].slice();
  }

  function bboxAround(center, radiusM) {
    const dLat = radiusM / metresPerDegLat();
    const dLng = radiusM / metresPerDegLng(center[0]);
    return [center[0] - dLat, center[1] - dLng, center[0] + dLat, center[1] + dLng];
  }

  // ---------- loading ----------

  function overpassQuery(center, radiusM) {
    const [s, w, n, e] = bboxAround(center, radiusM);
    const classes = "trunk|primary|secondary|tertiary|unclassified|residential|service";
    return `[out:json][timeout:30];way["highway"~"^(${classes})$"](${s},${w},${n},${e});out geom;`;
  }

  function normaliseOverpass(json) {
    const roads = [];
    for (const el of json.elements || []) {
      if (el.type !== "way" || !Array.isArray(el.geometry)) continue;
      const path = el.geometry.map((g) => [g.lat, g.lon]);
      if (path.length < 2) continue;
      roads.push({
        id: String(el.id),
        name: (el.tags && (el.tags.name || el.tags.ref)) || null,
        highway: (el.tags && el.tags.highway) || "unclassified",
        oneway: !!(el.tags && el.tags.oneway && el.tags.oneway !== "no"),
        path,
      });
    }
    return roads;
  }

  async function loadBaked() {
    // Depending on whether the server root is the repo or web/, the fixtures
    // directory sits at a different relative depth. Try both rather than
    // forcing one particular way of serving the app.
    const candidates = [
      (CFG.roadNetworkUrl || null),
      "../fixtures/road_network.json",
      "fixtures/road_network.json",
      "/fixtures/road_network.json",
    ].filter(Boolean);

    for (const url of candidates) {
      try {
        const res = await fetch(url);
        if (!res.ok) continue;
        const data = await res.json();
        if (data.roads && data.roads.length) {
          return { roads: data.roads, source: "baked" };
        }
      } catch (e) {
        // try the next candidate
      }
    }

    // Bundled copy, used by the standalone single-file build.
    const fx = window.NETRA_FIXTURES || {};
    if (fx.road_network && fx.road_network.roads && fx.road_network.roads.length) {
      return { roads: fx.road_network.roads, source: "bundled" };
    }
    return null;
  }

  async function loadLive(center, radiusM, timeoutMs = 20000) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(CFG.overpassUrl, {
        method: "POST",
        body: "data=" + encodeURIComponent(overpassQuery(center, radiusM)),
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        signal: ctrl.signal,
      });
      clearTimeout(t);
      if (!res.ok) throw new Error("overpass " + res.status);
      const roads = normaliseOverpass(await res.json());
      if (!roads.length) throw new Error("no ways returned");
      return { roads, source: "overpass" };
    } catch (e) {
      clearTimeout(t);
      return null;
    }
  }

  async function load() {
    const c = CFG.junction.center;
    return (await loadBaked()) || (await loadLive(c, CFG.bboxRadiusM)) || null;
  }

  // ---------- corridor selection ----------

  // Higher = more important road. Used to rank corridors so a long
  // residential lane can't outrank a trunk road through the junction.
  const CLASS_RANK = {
    trunk: 6, primary: 5, secondary: 4,
    tertiary: 3, unclassified: 2, residential: 2, service: 1,
  };

  /**
   * The monitored corridors: real roads that actually pass through the
   * junction.
   *
   * OSM splits one road into many ways, so we find the road *names* present
   * near the junction, then pull in every way carrying those names — not
   * just the ones inside the radius. Otherwise a highway renders as a stub.
   */
  function selectCorridors(roads) {
    const c = CFG.junction.center;
    const near = roads.filter((r) => minDistToPathM(c, r.path) <= CFG.corridorRadiusM);

    // Names present at the junction. Unnamed ways can only represent
    // themselves, so they stay keyed by id.
    const nearNames = new Set(near.filter((r) => r.name).map((r) => r.name));

    const groups = new Map();
    for (const r of roads) {
      let key = null;
      if (r.name && nearNames.has(r.name)) key = `name:${r.name}`;
      else if (!r.name && near.includes(r)) key = `id:${r.id}`;
      if (!key) continue;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(r);
    }

    const corridors = [];
    for (const [key, members] of groups) {
      members.sort((a, b) => pathLengthM(b.path) - pathLengthM(a.path));
      const primary = members[0];
      const totalLength = members.reduce((t, m) => t + pathLengthM(m.path), 0);
      corridors.push({
        id: "cor_" + key.replace(/[^a-zA-Z0-9]/g, "_").slice(0, 40),
        name: primary.name || `Unnamed ${primary.highway} near junction`,
        named: !!primary.name,
        highway: primary.highway,
        // Every member way, so the whole road is drawn and clickable.
        paths: members.map((m) => m.path),
        // Longest single run, used for placing events along the road.
        path: primary.path,
        lengthM: Math.round(totalLength),
        memberIds: members.map((m) => m.id),
      });
    }

    // Rank by road class, then by how much of the road is here.
    const rank = (c) => (CLASS_RANK[c.highway] || 0);
    const byImportance = (a, b) =>
      rank(b) !== rank(a) ? rank(b) - rank(a) : b.lengthM - a.lengthM;

    // A named road is almost always the one an engineer means. Showing four
    // real roads beats padding to six with backyard service lanes, so unnamed
    // ways are only used when nothing named is available at all.
    const named = corridors.filter((c) => c.named).sort(byImportance);
    const pool = named.length ? named : corridors.sort(byImportance);
    return pool.slice(0, CFG.monitoredCorridors);
  }

  /**
   * Distribute the fixture events across the real corridors and place each
   * one at a real coordinate on that road. Event attributes (TTC, vehicles,
   * conditions) are untouched — only `location` is replaced, so the fixture
   * stays the source of truth for everything that matters.
   *
   * Deterministic: same input always yields the same layout.
   */
  function snapEventsToCorridors(events, corridors) {
    if (!corridors.length) return events;
    const perCorridor = new Map(corridors.map((c) => [c.id, 0]));

    return events.map((ev, i) => {
      const corridor = corridors[i % corridors.length];
      const n = perCorridor.get(corridor.id);
      perCorridor.set(corridor.id, n + 1);
      // spread along the middle 80% of the road, golden-ratio stepped so
      // successive events on one corridor don't cluster
      const frac = 0.1 + ((n * 0.618 + 0.21) % 1) * 0.8;
      return { ...ev, location: pointAlongPath(corridor.path, frac), _corridor_id: corridor.id };
    });
  }

  return {
    load,
    loadLive,
    selectCorridors,
    snapEventsToCorridors,
    pointAlongPath,
    pathLengthM,
    minDistToPathM,
    distM,
    bboxAround,
    overpassQuery,
    ROAD_WEIGHTS,
  };
})();
