/* NETRA - sites: uploaded videos, their locations, and the severity pins.
 *
 * This replaces the road-corridor layer. A site is one location an operator
 * uploaded a clip for; the map shows one pin per site, coloured by the worst
 * conflict the pipeline found there.
 *
 * On what the pin means, because it matters when a judge asks: the coordinate
 * is the one the operator typed when uploading, not a fix derived from the
 * footage. TTC, PET, speeds and distances are the pipeline's measured output;
 * the map position is a site marker. Do not present a pin as a GPS position
 * for the vehicles.
 */
window.NetraSites = (() => {
  "use strict";

  const SEVERITY_COLORS = {
    severe: "#e5484d",
    conflict: "#f5b83d",
    clear: "#3ba55d",
    pending: "#4fb0ff",
    error: "#8fa0b5",
  };

  const SEVERITY_LABELS = {
    severe: "Severe conflict",
    conflict: "Conflict",
    clear: "No conflicts found",
    pending: "Processing",
    error: "Failed",
  };

  const KEY_DECIMALS = 5; // ~1 m; events from one clip share an exact coordinate

  function apiUrl(path) {
    const base = (window.NETRA_CONFIG && window.NETRA_CONFIG.apiBase) || "";
    return base.replace(/\/$/, "") + path;
  }

  function siteKey(loc) {
    if (!Array.isArray(loc) || loc.length < 2 || loc[0] == null || loc[1] == null) return null;
    return `${Number(loc[0]).toFixed(KEY_DECIMALS)},${Number(loc[1]).toFixed(KEY_DECIMALS)}`;
  }

  /* ---------------- API ---------------- */

  async function listJobs() {
    try {
      const res = await fetch(apiUrl("/api/videos"), { cache: "no-store" });
      if (!res.ok) return null;
      const env = await res.json();
      return env && env.ok ? env.data : null;
    } catch (e) {
      return null;
    }
  }

  async function getJob(jobId) {
    try {
      const res = await fetch(apiUrl(`/api/videos/${encodeURIComponent(jobId)}`), { cache: "no-store" });
      const env = await res.json();
      return env && env.ok ? env.data : null;
    } catch (e) {
      return null;
    }
  }

  async function deleteJob(jobId) {
    const res = await fetch(apiUrl(`/api/videos/${encodeURIComponent(jobId)}`), { method: "DELETE" });
    const env = await res.json();
    if (!env || !env.ok) throw new Error((env && env.error) || "Delete failed");
    return env.data;
  }

  /* Upload with real progress. XHR rather than fetch, because fetch still has
   * no upload-progress event and a 200 MB clip on a venue laptop needs one. */
  function upload(file, meta, onProgress) {
    return new Promise((resolve, reject) => {
      const params = new URLSearchParams({
        name: meta.name || "Unnamed site",
        lat: String(meta.lat),
        lon: String(meta.lon),
        filename: file.name,
        quality: meta.quality || "standard",
      });
      const xhr = new XMLHttpRequest();
      xhr.open("POST", apiUrl(`/api/videos?${params.toString()}`));
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      });
      xhr.addEventListener("load", () => {
        let env = null;
        try { env = JSON.parse(xhr.responseText); } catch (e) { /* below */ }
        if (!env) return reject(new Error(`Server returned ${xhr.status} with no JSON body.`));
        if (!env.ok) return reject(new Error(env.error || "Upload rejected."));
        resolve(env.data);
      });
      xhr.addEventListener("error", () => reject(new Error("Could not reach the server. Is it running on " + apiUrl("") + "?")));
      xhr.addEventListener("abort", () => reject(new Error("Upload cancelled.")));
      xhr.send(file);
    });
  }

  /* ---------------- grouping ---------------- */

  function severityOf(site) {
    if (site.events.some((e) => e.severity === "severe")) return "severe";
    if (site.events.length) return "conflict";
    if (site.jobs.some((j) => j.status === "running" || j.status === "queued")) return "pending";
    if (site.jobs.length && site.jobs.every((j) => j.status === "error")) return "error";
    return "clear";
  }

  /**
   * One site per uploaded video. A clip you upload puts exactly one pin at the
   * coordinate you gave it, and nothing else puts a pin on the map.
   *
   * Events attach to their job by the job id in the clip path the server wrote
   * (`/videos/<job_id>/...`), falling back to an exact coordinate match when
   * only one job sits there. An event with no job behind it - seeded test data,
   * or something pushed straight to POST /api/events by the edge - still shows
   * in the event list and the counts, but gets no pin: we have no clip and no
   * operator-entered location for it, so there is nothing honest to draw.
   */
  function buildSites(jobs, events) {
    const sites = [];
    for (const job of jobs || []) {
      const loc = job.location || [];
      const lat = Number(loc[0]);
      const lon = Number(loc[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      sites.push({
        id: job.job_id,
        name: (job.site_name || job.filename || "Unnamed site"),
        location: [lat, lon],
        job,
        jobs: [job],
        events: [],
      });
    }

    const byId = new Map(sites.map((s) => [s.id, s]));

    // Coordinate index, usable only where exactly one site sits on that key.
    const byCoord = new Map();
    for (const site of sites) {
      const k = siteKey(site.location);
      byCoord.set(k, byCoord.has(k) ? null : site);
    }

    const orphans = [];
    for (const ev of events || []) {
      let site = null;
      const m = /\/videos\/([^/]+)\//.exec(ev.clip || "");
      if (m && byId.has(m[1])) site = byId.get(m[1]);
      if (!site) site = byCoord.get(siteKey(ev.location)) || null;
      if (site) site.events.push(ev);
      else orphans.push(ev);
    }

    for (const site of sites) {
      site.severity = severityOf(site);
      site.severeCount = site.events.filter((e) => e.severity === "severe").length;
      site.conflictCount = site.events.length;
      site.processing = site.jobs.filter((j) => j.status === "running" || j.status === "queued").length;
      site.clip = site.job.annotated_url || null;
      site.minTtc = site.events.reduce(
        (m, e) => (typeof e.ttc_s === "number" && (m == null || e.ttc_s < m) ? e.ttc_s : m),
        null
      );
    }

    sites.orphanEvents = orphans;
    return sites;
  }

  /* ---------------- the pin ---------------- */

  function pinIcon(site, selected) {
    const color = SEVERITY_COLORS[site.severity] || SEVERITY_COLORS.error;
    const count = site.conflictCount;
    const badge = site.processing
      ? '<span class="pin-spin"></span>'
      : `<span class="pin-count">${count}</span>`;
    const cls = [
      "netra-pin",
      `sev-${site.severity}`,
      selected ? "selected" : "",
      site.processing ? "working" : "",
    ].join(" ");

    return L.divIcon({
      className: "netra-pin-wrap",
      html: `
        <div class="${cls}" style="--pin-color:${color}">
          <svg viewBox="0 0 32 44" width="32" height="44" aria-hidden="true">
            <path d="M16 43C16 43 30 26.6 30 15.8 30 7.6 23.7 1 16 1S2 7.6 2 15.8C2 26.6 16 43 16 43Z"
                  fill="var(--pin-color)" stroke="#0b1017" stroke-width="2"/>
            <circle cx="16" cy="15.5" r="8.5" fill="#0b1017" opacity="0.82"/>
          </svg>
          ${badge}
        </div>`,
      iconSize: [32, 44],
      iconAnchor: [16, 43],
      popupAnchor: [0, -38],
    });
  }

  function statusLine(site) {
    if (site.processing) {
      const job = site.jobs.find((j) => j.status === "running" || j.status === "queued");
      return job ? job.message || "Processing…" : "Processing…";
    }
    if (site.severity === "error") {
      const job = site.jobs.find((j) => j.status === "error");
      return job ? job.message : "Pipeline failed.";
    }
    if (!site.conflictCount) return "No conflicts above threshold.";
    return `${site.conflictCount} conflict${site.conflictCount === 1 ? "" : "s"}, ${site.severeCount} severe.`;
  }

  return {
    SEVERITY_COLORS,
    SEVERITY_LABELS,
    siteKey,
    listJobs,
    getJob,
    deleteJob,
    upload,
    buildSites,
    pinIcon,
    statusLine,
  };
})();
