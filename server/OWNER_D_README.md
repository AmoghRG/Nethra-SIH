# NETRA Server — Owner D Implementation

**Modules:** M6 (Weather & Time Enrichment) + M7 (Event Ingest, Buffer, Scoring)  
**Owner:** D  
**Ownership Map:** `server/api/`, `server/enrich/`, `server/scoring/`

---

## Overview

Owner D owns the **server side** of NETRA: ingesting conflict events from the edge (M2, M3), enriching them with weather and time context (M6), storing them reliably offline and online (M7), and exposing a REST API for the frontend (M11).

### Key Principles

1. **Server enriches; edge detects.** Weather, light state, and temporal context are attached here, not on the camera. The edge stays simple.
2. **Offline resilience.** If Postgres fails, events buffer to SQLite and replay automatically when the server recovers.
3. **Idempotent ingestion.** Replaying a buffer twice produces no duplicates.
4. **Event-only data.** Only ~300-byte structured events leave the site; no video, no images.

---

## Architecture

### Stack

- **FastAPI** — REST API server
- **PostgreSQL + PostGIS** — Persistent event store (spatial indexing optional)
- **SQLite** — Offline buffer (single-file, no setup required)
- **Astral** — Offline light-state computation (sunrise/sunset)
- **Uvicorn** — ASGI server

### Data Flow

```
Edge (M3 output)
    ↓ POST /api/events
    ↓ [M6: Enrich with weather & light]
    ↓ [M7: Try Postgres → buffer if fail]
    ↓ Background replay thread
    ↓
Postgres (durable) + SQLite buffer (resilience)
    ↓
GET /api/events (M11 dashboard)
GET /api/segments (map data)
GET /api/health (monitoring)
```

---

## Files

| File | Purpose |
|------|---------|
| `netra_server.py` | Main server; all M6 + M7 logic |
| `netra_fixtures.py` | Fixture server; runs hours 2–14 before live API |
| `netra_db_setup.py` | DB initialization, buffer test, condition scoring test |
| `requirements_server.txt` | Python dependencies |
| `OWNER_D_README.md` | This file |

---

## Getting Started

### 1. Prerequisites

```bash
# PostgreSQL (macOS)
brew install postgresql@15

# Or Docker
docker run -e POSTGRES_PASSWORD=postgres -d postgres:15

# Python 3.10+
python3 --version
```

### 2. Install Dependencies

```bash
pip install -r requirements_server.txt
```

### 3. Initialize Database

```bash
python netra_db_setup.py
```

This will:
- Create the `netra` database
- Set up the `events` table with indices
- Initialize SQLite buffer
- Run offline buffer test and condition multiplier test

### 4. Generate Fixtures (for hours 2–14)

```bash
python netra_fixtures.py gen
python netra_fixtures.py
# Server running on http://localhost:8001
```

### 5. Start Main Server (after fixtures, around hour 14)

```bash
python netra_server.py
# Server running on http://localhost:8000
```

---

## Contract Schemas (Frozen at Hour 2)

### ConflictEvent

Input from edge (M3). `conditions` is null until enriched by M6.

```json
{
  "event_id": "evt_00417",
  "time": "2026-08-28T19:47:12Z",
  "location": [13.0106, 74.7943],
  "type": "crossing conflict",
  "ttc_s": 0.8,
  "pet_s": 1.4,
  "severity": "severe",
  "vehicle_a": {"type": "motorcycle", "speed_kmh": 47, "direction": "normal"},
  "vehicle_b": {"type": "car", "speed_kmh": 31, "direction": "against flow"},
  "conditions": null,
  "detection_quality": 0.71
}
```

### Conditions (After M6 Enrichment)

Added server-side only. Edge leaves this null.

```json
{
  "light": "dark",
  "hour_of_day": 19,
  "weekday": "Thursday",
  "peak_hour": 0,
  "weather": "light rain",
  "surface": "wet",
  "rain_factor": 1.3,
  "detection_quality": 0.71
}
```

**Hard rules:**
- `conditions` is written **only** by the server.
- Never assign blame or fault.
- `severity` is derived, never hand-set.

---

## API Reference

### POST /api/events

**Ingest one or more events.**

```bash
curl -X POST http://localhost:8000/api/events \
  -H "Content-Type: application/json" \
  -d '[
    {
      "event_id": "evt_123",
      "time": "2026-08-29T19:47:12Z",
      "location": [13.0106, 74.7943],
      "type": "crossing conflict",
      "ttc_s": 0.75,
      "pet_s": 1.2,
      "severity": "severe",
      "vehicle_a": {"type": "motorcycle", "speed_kmh": 47, "direction": "north"},
      "vehicle_b": {"type": "car", "speed_kmh": 31, "direction": "east"},
      "conditions": null,
      "detection_quality": 0.82
    }
  ]'
```

**Response:**

```json
{
  "ok": true,
  "data": [
    {"event_id": "evt_123", "ok": true}
  ],
  "error": null
}
```

**Behavior:**
- Event is enriched with weather & light (M6)
- Stored in PostgreSQL
- If Postgres fails, buffered to SQLite and replayed automatically
- Idempotent on `event_id` — duplicate ingestion produces no duplicates

---

### GET /api/events

**Retrieve events with optional filtering.**

```bash
# All events
curl http://localhost:8000/api/events

# Filter by time range
curl "http://localhost:8000/api/events?from_time=2026-08-29T19:00:00Z&to_time=2026-08-29T20:00:00Z"

# Filter by light condition
curl "http://localhost:8000/api/events?light=dark"

# Filter by weather
curl "http://localhost:8000/api/events?weather=light%20rain"

# Combined
curl "http://localhost:8000/api/events?light=dark&weather=light%20rain"
```

**Response:**

```json
{
  "ok": true,
  "data": [
    {
      "event_id": "evt_123",
      "time": "2026-08-29T19:47:12",
      "location": [13.0106, 74.7943],
      "type": "crossing conflict",
      "ttc_s": 0.75,
      "pet_s": 1.2,
      "severity": "severe",
      "vehicle_a": {...},
      "vehicle_b": {...},
      "conditions": {...},
      "detection_quality": 0.82
    }
  ],
  "error": null
}
```

---

### GET /api/events/{event_id}/narrative

**Get plain-English write-up for one event (M8 integration).**

```bash
curl http://localhost:8000/api/events/evt_123/narrative
```

**Response:**

```json
{
  "ok": true,
  "data": {
    "narrative": "At 7:47 pm on Thursday, after dark and in light rain, a motorcycle travelling at 47 km/h approached from the north as a car entered from the east against the usual flow of traffic. The two came within 0.8 seconds of collision. This junction has recorded 23 similar conflicts this month, 16 of them in wet conditions after sunset."
  },
  "error": null
}
```

*Note: Currently hardcoded. M8 will integrate to generate narratives dynamically.*

---

### GET /api/segments

**Get scored road segments for the map (M11).**

```bash
curl http://localhost:8000/api/segments
```

**Response:**

```json
{
  "ok": true,
  "data": [
    {
      "segment_id": "seg_13.01_74.79",
      "location": [13.0106, 74.7943],
      "risk_score": 0.68,
      "conflict_count_24h": 8,
      "conditions_applied": {"light": "mixed", "weather": "mixed"}
    }
  ],
  "error": null
}
```

**Risk Scoring Formula:**

```
risk = base_risk(location)
       × severity_multiplier
       × rain_factor
       × night_factor
       × peak_hour_factor
       × detection_quality_normalizer
```

Example:
- Base risk: 0.5
- Severe conflict: ×2.0
- Light rain: ×1.3
- Night: ×1.5
- Peak hour: ×1.3
- Quality: ×0.8

→ **risk = 0.5 × 2.0 × 1.3 × 1.5 × 1.3 × 0.8 = 1.0 (clamped)**

---

### GET /api/health

**Pipeline status (for live panel, M11 P2).**

```bash
curl http://localhost:8000/api/health
```

**Response:**

```json
{
  "ok": true,
  "data": {
    "postgres": true,
    "buffer": 0,
    "timestamp": "2026-08-29T19:47:12.123456"
  },
  "error": null
}
```

**Interpretation:**
- `postgres: true` → PostgreSQL is online
- `buffer: 0` → No events buffered (or successfully replayed)
- `buffer: > 0` → Postgres offline; events waiting in SQLite

---

## M6: Weather & Time Enrichment

### How It Works

For every event, M6 adds:

| Field | Source | Method |
|-------|--------|--------|
| `light` | **Astral (offline)** | Sunrise/sunset for location & date |
| `hour_of_day` | Event timestamp | Direct |
| `weekday` | Event timestamp | Direct |
| `peak_hour` | Hour range | 7–10 AM, 5–8 PM → peak_hour=1 |
| `weather` | Cached lookup table | Dict keyed by hour + condition |
| `surface` | Cached lookup table | Wet (rain) or dry |
| `rain_factor` | Cached lookup table | 1.0 (dry) to 1.8 (heavy rain) |

### Weather Lookup

Edit `WEATHER_LOOKUP` in `netra_server.py`:

```python
WEATHER_LOOKUP = {
    "00_dry": {"rain_factor": 1.0, "surface": "dry", "weather": "clear"},
    "00_light_rain": {"rain_factor": 1.3, "surface": "wet", "weather": "light rain"},
    "00_heavy_rain": {"rain_factor": 1.8, "surface": "wet", "weather": "heavy rain"},
    # Add more as needed: {hour}_{condition} → factors
}
```

### Astral (Offline)

Uses the `astral` library to compute sunrise/sunset from date + location. No API call required.

```python
from astral import sun, LocationInfo

location = LocationInfo(
    name="Demo",
    region="India",
    timezone="Asia/Kolkata",
    latitude=13.0106,
    longitude=74.7943
)

sun_data = sun(location.observer, date)
# sun_data["sunrise"], sun_data["sunset"], etc.
```

---

## M7: Event Ingest & Buffer

### Offline Resilience

**Problem:** If Postgres is down, edge events are lost.

**Solution:**

1. Try to store in PostgreSQL
2. On failure, buffer to SQLite
3. Background thread replays buffer every 30s
4. On success, buffer is cleared

### SQLite Buffer Schema

```sql
CREATE TABLE pending_events (
    event_id TEXT PRIMARY KEY,
    event_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Idempotency

Events are keyed by `event_id`. Replaying the same event twice produces no duplicates:

```python
cur.execute("""
    INSERT INTO events (...) VALUES (...)
    ON CONFLICT (event_id) DO NOTHING;  -- PostgreSQL
""")
```

Or use explicit check:

```python
cur.execute("SELECT 1 FROM events WHERE event_id = %s", (event.event_id,))
if cur.fetchone():
    return True  # Already exists; not an error
```

### Testing Offline Behavior

```bash
# 1. Start server
python netra_server.py &

# 2. Stop Postgres (simulate network failure)
sudo systemctl stop postgresql

# 3. Send event (will buffer)
curl -X POST http://localhost:8000/api/events -d '[{...}]'

# 4. Check buffer
sqlite3 ./data/event_buffer.db "SELECT COUNT(*) FROM pending_events;"

# 5. Restart Postgres
sudo systemctl start postgresql

# 6. Wait 30s for replay thread
sleep 30

# 7. Verify event in Postgres
psql netra -c "SELECT COUNT(*) FROM events;"
```

---

## Integration Points

### With M3 (Edge — Conflict Detection)

- M3 outputs `tracks.jsonl` → runs conflict detection
- M3 emits `ConflictEvent` records via POST /api/events
- M6 enriches immediately; event stored to Postgres or buffer

### With M8 (Narration)

- M8 consumes `ConflictEvent` JSON
- Generates plain-English narrative
- Stores narrative back to `events` table (or cache)
- M11 fetches via `/api/events/{id}/narrative`

### With M9 (Ground Truth Evaluation)

- M9 compares system output against manual labels
- Uses `GET /api/events` to fetch all system-detected events
- Scores: recall, false positives, mean TTC

### With M11 (Frontend Dashboard)

- M11 calls `/api/events?light=dark&weather=rain` for filtered list
- Calls `/api/segments` for map layers
- Calls `/api/health` for live status
- Swaps from fixture server (port 8001) to live server (port 8000) at hour 14

---

## Deployment Checklist

### Hour 2 (Before Development Starts)

- [ ] Database initialized (`python netra_db_setup.py`)
- [ ] Fixtures generated (`python netra_fixtures.py gen`)
- [ ] Fixture server running on port 8001
- [ ] M11 (frontend) building against `http://localhost:8001`

### Hour 8 (Gate 1 — Vertical Slice)

- [ ] Main server running on port 8000
- [ ] M3 can POST events to `/api/events`
- [ ] Events enriched with weather + light
- [ ] Events queryable via `/api/events`

### Hour 14 (Gate 2 — End-to-End)

- [ ] M11 swaps to `http://localhost:8000`
- [ ] Dashboard renders live events
- [ ] Segment scoring works
- [ ] Offline buffer tested (network disconnect → auto-replay)

### Hour 19 (Feature Freeze)

- [ ] All P0 APIs working
- [ ] No schema changes after this point

### Hour 22 (Full Rehearsal)

- [ ] Demo works with network unplugged
- [ ] Cached narratives for all demo events

---

## Troubleshooting

### PostgreSQL Connection Refused

```
psycopg2.OperationalError: could not connect to server
```

**Fix:**

```bash
# Start Postgres
sudo systemctl start postgresql

# Or if using Homebrew on macOS
brew services start postgresql@15

# Or Docker
docker run -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres:15
```

### Buffer Growing (Not Replaying)

**Symptom:** `/api/health` shows `buffer: 100+` but doesn't decrease.

**Causes:**
- Postgres still down (check `docker logs`)
- Event JSON malformed (check logs)
- Unique constraint violation

**Debug:**

```bash
sqlite3 ./data/event_buffer.db ".schema pending_events"
sqlite3 ./data/event_buffer.db "SELECT COUNT(*) FROM pending_events;"
sqlite3 ./data/event_buffer.db "SELECT event_json FROM pending_events LIMIT 1;" | python3 -m json.tool
```

### Event Duplicates

**Symptom:** Same `event_id` appears twice in query results.

**Cause:** Buffer replay and direct ingest happened simultaneously.

**Fix:** Ensure `ON CONFLICT (event_id) DO NOTHING` in PostgreSQL, or check before insert.

### Astral Timezone Error

```
UnknownTimeZoneError: Asia/Kolkata
```

**Fix:** Update tzdata or use UTC:

```python
location = astral.LocationInfo(
    name="Demo",
    region="India",
    timezone="UTC",  # Use UTC as fallback
    latitude=13.0106,
    longitude=74.7943
)
```

---

## Performance Notes

### Event Ingestion Rate

- Single machine PostgreSQL: ~100–500 events/sec
- Batch operations (POST array of events): 10× faster

### SQLite Buffer Capacity

- 10,000 events ≈ 3 MB (if ~300 bytes/event)
- No practical limit; auto-cleanup after replay

### Query Latency

- `GET /api/events` (all): ~50–100ms on 10k events
- With filters: ~10–20ms (index used)
- Spatial queries (PostGIS): ~5–10ms

---

## Scaling & Production

### Multi-Site Deployment

Each site (camera) has its own `event_id` prefix:

```
location_1: evt_loc1_00001, evt_loc1_00002, ...
location_2: evt_loc2_00001, evt_loc2_00002, ...
```

Query by location:

```sql
SELECT * FROM events WHERE event_id LIKE 'evt_loc1_%';
```

### Replication

PostgreSQL replication (streaming):

```bash
# Primary: netra_main.example.com
# Replica: netra_backup.example.com

# On replica:
psql -h netra_main.example.com ...
```

### Monitoring

Add Prometheus exports:

```python
from prometheus_client import Counter, Gauge

events_ingested = Counter("events_ingested_total", "Total events")
buffer_size = Gauge("buffer_size", "Pending events in SQLite")

@app.post("/api/events")
async def ingest_events(...):
    # ...
    events_ingested.inc(len(events))
```

---

## References

- **FastAPI:** https://fastapi.tiangolo.com
- **PostgreSQL:** https://www.postgresql.org
- **Astral:** https://astral.readthedocs.io
- **PostGIS:** https://postgis.net
- **PRD (Contract Schemas):** Section 5 of NETRA PRD

---

## Owner D Contact

Questions or integration issues? Refer to PRD Section 8 for cross-module responsibilities and Section 9 for the 24-hour timeline.

**M6 & M7 are P0 and P0 respectively — protect these modules; don't add features past hour 19.**
