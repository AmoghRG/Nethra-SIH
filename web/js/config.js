/* NETRA dashboard configuration.
 * Everything site-specific lives here so nothing is buried in app.js.
 */
window.NETRA_CONFIG = {
  // The junction under analysis. Replace with the real demo-video location
  // once it's chosen (PRD Section 12, Q1 — still open at time of writing).
  junction: {
    name: "Pumpwell Circle, Mangaluru",
    note: "NH-66 × NH-75 — placeholder site until the demo clip's location is fixed.",
    center: [12.86889, 74.86389],
  },

  // Half-size of the road-network fetch box, in metres.
  bboxRadiusM: 800,

  // Corridors within this radius of the junction centre become the
  // "monitored" roads that get risk-coloured. Everything else renders
  // as dim context.
  corridorRadiusM: 260,
  monitoredCorridors: 6,

  carto: {
    // Basemap key from https://carto.com/basemaps/apikey — free, ~1 minute,
    // no CARTO account needed. This is NOT your CARTO Platform API token:
    // that one is for querying carto_dw data sources and will not
    // authenticate basemap tiles. Leave blank to run without a key
    // (tiles still load, but CARTO watermarks them).
    key: "cb1_2jia_1_93e45b0074f70164ae508e69",
    style: "dark_all", // dark_all | dark_nolabels | light_all | rastertiles/voyager
  },

  // Where D's FastAPI server lives (server/api/netra_server.py, default
  // port 8000). The dashboard is usually served from a different port, so
  // this cannot be a relative path — leave it as "" only if you are serving
  // the frontend from the API itself.
  apiBase: "http://localhost:8000",

  // Explicit path to the baked road network, if the defaults don't resolve.
  // Leave null to try ../fixtures/, fixtures/ and /fixtures/ in turn.
  roadNetworkUrl: null,

  // Overpass mirror used to fetch real street centrelines when
  // fixtures/road_network.json is absent.
  overpassUrl: "https://overpass-api.de/api/interpreter",
};
