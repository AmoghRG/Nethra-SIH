# HTTP API contract

**Owner: D (M7).** STUB — D owns the implementation and the detail of this
document. What is recorded here is the surface that other owners code against
from hour 2, per parent PRD §5.5. E builds the entire dashboard against the
fixture server; B's edge emitter targets `POST /api/events` and nothing else.

| Method | Path | Purpose | Owner |
|---|---|---|---|
| `POST` | `/api/events` | Ingest one or many ConflictEvents | D |
| `GET` | `/api/events?from=&to=&light=&weather=` | Filtered event list | D |
| `GET` | `/api/events/{id}/narrative` | Plain-English write-up | F |
| `GET` | `/api/segments` | Scored road segments for the map | D |
| `GET` | `/api/health` | Pipeline status for the live panel | D |

Response envelope for **every** endpoint:

```json
{"ok": true, "data": {}, "error": null}
```

## What the edge assumes of `POST /api/events`

These are the only assumptions `edge/emit/uploader.py` makes. If D needs to
change one, it is a contract change and needs F's sign-off.

- Accepts a **JSON array** of ConflictEvent objects as the request body
  (the emitter batches; it does not send bare single objects).
- **Idempotent on `event_id`.** Replaying a buffered batch twice must not
  double-count. This is what makes the offline buffer safe to retry blindly.
- Any 2xx means accepted and durable. The edge marks those events sent and
  never resends them.
- Any non-2xx, timeout, or connection failure means not accepted. The edge
  buffers to SQLite and retries with exponential backoff. It is D's server
  going down that this path exists for, and killing it mid-run is a
  rehearsed demo beat.
