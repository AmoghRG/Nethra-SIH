/* NETRA dashboard configuration.
 * Everything site-specific lives here so nothing is buried in app.js.
 */
window.NETRA_CONFIG = {
  // Where the map opens. Not a claim about any site — every real location
  // arrives with an uploaded clip.
  junction: {
    name: "NETRA Risk Map",
    note: "Opening view only. Sites come from uploaded videos.",
    center: [12.86889, 74.86389],
  },

  // Where the map opens before any clip has been uploaded. Every pin after
  // that comes from the location the operator gave with the video.
  defaultZoom: 16,

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

};
