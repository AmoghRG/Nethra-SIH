# web/ — M11 risk map dashboard

**Owner: E. Priority P0.**

The surface the judge actually looks at. Open `http://localhost:8080/web/` with
the API on port 8000, or double-click `demo/netra-dashboard-standalone.html`.

## What changed: sites, not roads

The dashboard used to colour road corridors pulled from OpenStreetMap and snap
each event onto the nearest centreline. That is gone. The map now shows **one
severity pin per site**, and a site is created by uploading a video together
with the location it was filmed at.

Why the change is an improvement and not just a simplification:

- **The snapped positions were invented.** An event's coordinate came from
  `calibration.location` — one point for the whole clip — and the old code then
  slid it along a road to make the map look populated. A judge asking "how do
  you know that conflict happened *there*" had no good answer. One pin at the
  location the operator entered says exactly as much as we actually know.
- **The OSM fetch was an internet dependency the PRD does not have.** S6 says
  every demo step works with the cable unplugged. `tools/fetch_roads.py`,
  `fixtures/road_network.json` and the Overpass fallback are no longer on any
  path the dashboard takes.
- `web/js/roads.js`, `tools/fetch_roads.py` and `fixtures/road_network.json`
  are left in the tree, unreferenced, in case the corridor view is wanted back.

## One pin per uploaded video, and nothing else

A pin means: someone uploaded a clip and typed a location for it. Uploading a
video adds exactly one pin at that coordinate, and no other source puts a pin
on the map.

Events attach to their upload by the job id in the clip path the server writes
(`/videos/<job_id>/...`), falling back to an exact coordinate match when only
one upload sits there. An event with no upload behind it — seeded test rows, or
something posted straight to `/api/events` by the edge — still appears in the
event list and the counts, but gets **no pin**. There is no clip and no
operator-entered location for it, so there is nothing honest to draw.

Two consequences worth knowing:

- **The database now starts empty.** `seed_sqlite_if_empty()` used to load
  `fixtures/events.sample.json` on any empty boot, which put sample conflicts
  on the map at coordinates nobody entered, indistinguishable from measured
  output. It is disabled. `tools/seed_events.py` still exists for deliberate
  test data, and prefixes its rows `evt_seed_` so they can be deleted again.
- **The standalone build shows no pins**, because it has no uploads. Its
  bundled fixture events still drive the event list, charts and narratives, so
  the offline fallback still demonstrates every panel — it just does not claim
  a location it does not have.

**Say this out loud in the demo:** the pin is a site marker, not a GPS fix on
the vehicles. Distances, speeds, TTC and PET are the pipeline's measured
metric output; the coordinate is what the operator typed.

## Add video

`+ Add video` in the top bar takes a clip, a site name, and a latitude and
longitude — typed, taken from the map centre, or picked by clicking the map.
`POST /api/videos` streams the file to disk and runs `ingest_video.py` as a
child process. The pin appears immediately in blue and polls every 3 s; when
the run finishes it turns red, amber or green and the events, charts and
narratives fill in.

Processing depth is a **frame budget**, not a quality knob:

| Preset | Frames | Detector |
|---|---|---|
| Quick | 600 | `yolov8n.pt` |
| Standard | 2,400 | `yolov8m.pt` |
| Full | whole clip | `yolov8m.pt` |

Quick exists so a demo finishes while someone is watching. Never quote a
recall or false-positive figure from a Quick run.

## The annotated clip has to be re-encoded

`ingest_video.py` renders the annotated video with OpenCV's `VideoWriter`,
which writes **MPEG-4 Part 2** with the `mp4v` tag — the only codec OpenCV can
rely on having. It is a valid `.mp4` and **no browser will play it**: Chrome,
Edge and Firefox decode H.264, not MPEG-4 Part 2, so `<video>` fails with a
format/MIME error and you get a black box.

So after a run the server re-encodes the render to H.264 (yuv420p, faststart,
capped at 1280 wide) **in place**, which keeps the URL already stored on the
job and on every event correct. A 10-second 2392x1352 render goes from 31 MB to
about 2 MB, which also matters when it has to stream over loopback in front of
judges.

It needs ffmpeg — from `PATH` if you have it, otherwise the binary bundled by
`imageio-ffmpeg`, now in `requirements.txt`. If neither is there the step is
skipped, the job says so in its message, and the player falls back to a note
and a direct link rather than a silent black box. The events are unaffected
either way; the clip is a view of them.

## Files

| File | Role |
|---|---|
| `index.html` | Layout, filters, the add-video modal, the diagnostics modal |
| `js/config.js` | Opening map view, CARTO key, `apiBase` |
| `js/sites.js` | Upload client, site grouping, pin icons |
| `js/app.js` | Everything else — data load, map, charts, event list, modals |
| `js/fixtures.js` | Generated by `tools/gen_fixtures.py`; the offline fallback |
| `css/styles.css` | All styling |

## Acceptance

- Renders from fixtures with the backend stopped, with an offline badge. No
  blank screen on API failure.
- Every panel handles null. A missing signal cycle hides one card.
- A failed pipeline run shows as a grey pin and an error message on the job,
  never a 500 or a silent nothing.
- Tested at the projector resolution before hour 22.

## Contract

`contracts/http_api.md` plus the four `/api/videos` routes documented in
`server/PATCHES_TO_OWNER_D.md`. Envelope is
`{"ok": true, "data": …, "error": null}` throughout.
