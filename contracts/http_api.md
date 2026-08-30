# HTTP API (PRD 5.5)

Base URL: `http://localhost:8000` (owner D, `server/api/netra_server.py`).
The dashboard reads this from `web/js/config.js` → `apiBase`.

Response envelope for every endpoint:

```json
{"ok": true, "data": {}, "error": null}
```

Note `ok: true` with a `200` still occurs on handler errors in some paths —
consumers should check `ok`, not just the status code.

| Method | Path | Purpose | Owner |
|---|---|---|---|
| `POST` | `/api/events` | Ingest one or many ConflictEvents | D |
| `GET` | `/api/events?from_time=&to_time=&light=&weather=` | Filtered event list | D |
| `GET` | `/api/events/{id}/narrative` | Plain-English write-up | F (D serves a template until M8 lands) |
| `GET` | `/api/segments` | Scored location clusters | D |
| `GET` | `/api/health` | Pipeline status | D |

## Verified response shapes

These were captured from the running server, not from the spec.

### GET /api/events → `data: [...]`

Matches `conflict_event.schema.json` exactly, with `location` as `[lat, lon]`.
`conditions` is populated by M6 on ingest and adds `hour_of_day`, `weekday`,
`peak_hour` and `rain_factor` beyond the PRD's listed fields.

### GET /api/segments → `data: [...]`

```json
{
  "segment_id": "seg_12.8686_74.8624",
  "location": [12.8686, 74.8624],
  "risk_score": 1.5,
  "conflict_count_24h": 4,
  "conditions_applied": {"light": "mixed", "weather": "mixed"}
}
```

**These are point clusters, not road geometry.** Events are grouped onto a
rounded lat/lon grid (`SEGMENT_GRID_DECIMALS`, default 4 ≈ 11 m); there is no
centreline, so segments cannot be drawn as roads.

`risk_score` is `AVG(severe ? 2.0 : 1.0)`, so it ranges 1.0–2.0 — it is **not**
a 0–100 score, and it does not currently use `compute_risk_score()` (which is
defined in the server but never called).

Consequence for M11: the dashboard takes corridor geometry from OpenStreetMap
and computes per-corridor risk from the full event list, using this endpoint
only as a cross-check shown in the health panel. If D later attaches geometry
to segments, M11 can switch to it — see `applyServerRisk` in `web/js/app.js`.

### GET /api/health → `data: {...}`

```json
{"postgres": true, "buffer": 0, "timestamp": "2026-08-28T19:47:12"}
```

Not the `pipeline_status` / `frame_rate_fps` / `escalations_per_min` shape the
PRD's live panel implies. M11 renders whichever of the two shapes it receives.

### GET /api/events/{id}/narrative → `data: {"narrative": "..."}`

A server-side template until M8 replaces it.
