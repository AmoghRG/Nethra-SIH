# Patches applied to owner D's code

**Applied by: owner E (M11). Please review before Gate 2.**

The PRD's Section 8 rule is that only a directory's owner edits it. I broke
that rule here. These changes are all in `server/api/netra_server.py`, each
marked inline with a `[PATCH E-n]` comment so you can find, review, revert or
rewrite them. Nothing else of yours was touched, and no file was deleted.

Reason: seven of these are hard blockers found while wiring M11 to the live
API. Without them the frontend cannot connect and the ingest path does not
work at all. Four were only visible by running the server against a real
Postgres, not by reading the code.

| # | Severity | What was wrong |
|---|---|---|
| E-1 | **Blocker** | No CORS middleware |
| E-2 | **Blocker** | `/api/segments` used PostGIS functions on a native `POINT` column |
| E-3 | Major | Segment grid was ~1.1 km, collapsing a junction into one cluster |
| E-4 | Major | Narrative named `vehicle_b` first and `vehicle_a` second |
| E-5 | Minor | Narrative crashed on a null `ttc_s` |
| E-6 | **Blocker** | Timezone crash aborted every `POST /api/events` |
| E-7 | **Blocker** | Failed PostGIS extension left the transaction aborted, so the events table was never created |
| E-8 | **Blocker** | Double JSON decode broke `GET /api/events` and the narrative endpoint |
| E-9 | **Blocker** | `POINT` parsed as a string, so every event's coordinates were garbage |
| E-10 | **Major** | Weather lookup always missed, so every event was "clear" and the rain filter (S5) returned nothing |

---

## E-1 — CORS missing

The dashboard is served from a different origin (`:8080`, or `file://`), so
the browser blocked every request before it reached a handler. Added
`CORSMiddleware` with an open policy, which is appropriate here because the
service is local, unauthenticated by design (PRD Section 3 puts auth out of
scope) and read-mostly. Narrow it if this is ever exposed beyond localhost.

## E-2 — `/api/segments` returned `ok: false` on every call

The table declares `location POINT` — a *native Postgres* point. The query
used `ST_Y(location)` / `ST_X(location)`, which are PostGIS functions
expecting a `geometry`, so Postgres raised `function st_y(point) does not
exist`. Switched to native subscripting: `location[0]` is x/lon,
`location[1]` is y/lat.

Worth deciding deliberately: either commit to PostGIS and store `geometry`,
or stay on native `POINT` and avoid PostGIS functions. Mixing them is what
caused this. The PRD says SQLite is an acceptable substitute anyway, so
native `POINT` is a perfectly defensible choice.

## E-3 — grid resolution

`ROUND(lat, 2)` is 0.01 degrees, about 1.1 km. Every conflict at one junction
landed in a single cluster, so `/api/segments` returned one row and the map
had nothing to differentiate. Now `SEGMENT_GRID_DECIMALS = 4` (~11 m), which
separates the approaches. Verified: 18 events across four arms produced 1
cluster before, 4 after.

Also widened the window from 24 hours to 30 days (`SEGMENT_WINDOW`). A demo
clip is one evening, so a 24-hour window silently returns nothing the next
morning — which would have looked like a broken map on finale day.

## E-4, E-5 — narrative endpoint

`row[7]` is `vehicle_a` and `row[8]` is `vehicle_b`; the template used them
the wrong way round, so every write-up named the wrong vehicle first. And
`f"{row[4]:.1f}"` raises `TypeError` when `ttc_s` is null.

Note this is a stopgap: M8 (owner F) owns narration, and the PRD requires a
field-presence assertion that every number in the prose traces to a field in
the record. Worth handing over rather than growing here.

## E-6 — timezone crash killed all ingest

`astral.sun.sun()` returns timezone-aware datetimes. Event times arrive as
naive ISO strings, and comparing naive to aware raises
`can't compare offset-naive and offset-aware datetimes`. Every single
`POST /api/events` failed with a 500.

Naive timestamps are now assumed local to the observer (Asia/Kolkata), which
matches what the edge actually records.

**Note:** `WeatherEnricher.__init__` defaults to `location_lat=13.0106,
location_lon=74.7943`. That is not Pumpwell — it is roughly 16 km north. It
only shifts sunrise/sunset by seconds so it will not visibly break anything,
but it should track the real demo site once PRD Q1 is settled. Left alone as
it is a judgement call in your lane.

## E-7 — events table was never created

`CREATE EXTENSION postgis` fails when PostGIS is not installed. The bare
`except` swallowed it but left the connection in an aborted transaction, so
the `CREATE TABLE` immediately after failed with `current transaction is
aborted, commands ignored until end of transaction block`.

The server then logged "initialized" and looked healthy while storing
nothing. Added `conn.rollback()` in the handler.

This is the most dangerous one: it fails silently and only shows up as an
empty dashboard.

## E-8 — double JSON decode

`psycopg2` already decodes `JSONB` columns into Python dicts. Calling
`json.loads()` on them raises `the JSON object must be str, bytes or
bytearray, not dict`, which broke `GET /api/events` and the narrative
endpoint outright. Added an `as_dict()` helper that accepts either form, so
it also works with the SQLite buffer where the values really are strings.

## E-9 — coordinates were garbage

`psycopg2` returns a native `POINT` as the string `"(lon,lat)"`, not a tuple.
The code did `[row[2][1], row[2][0]]`, which indexed two *characters* of that
string — every event came back with a location like `['7', '(']`. Added
`parse_point()`, returning `[lat, lon]` per the contract.

## E-10 — every event came back "clear", breaking the condition filters

`get_weather()` built the key `f"{hour:02d}_{condition}"`, but `WEATHER_LOOKUP`
only contained `00_*` entries. So every lookup for any hour except midnight
missed and fell through to `WEATHER_LOOKUP["00_dry"]`.

The effect: every ingested event was recorded as clear and dry regardless of
the hour, including the ones the code explicitly intended to be rainy. The
weather condition filter therefore returned zero events, which silently
disables PRD success criterion **S5** — "the same junction shows different
risk under different condition filters" — and with it the visible payoff of
M6.

Fixed by keying `WEATHER_LOOKUP` on the condition alone, and adding
`WEATHER_CACHE`, a per-hour table keyed `"YYYY-MM-DD HH"`. This is what M6
actually asks for: "Weather from a cached lookup table committed to the repo.
A live API is a bonus path, never the demo path."

The cached entries currently describe a wet evening on 2026-08-28. **They are
placeholders and should be replaced with real observations for the actual
demo clip's date and location.** Hours with no cached entry fall back to a
coarse diurnal guess and are tagged `weather_estimated: true` in
`conditions`, so guessed weather can be told apart from observed weather
rather than quietly presented as measurement.

Verified: 40 seeded events now yield clear / light rain / heavy rain and
dry / wet, and the filters return distinct sets.

---

## Not fixed — your call

- **`compute_risk_score()` is dead code.** It implements the PRD's
  `risk = base × rain × night × peak` model (M6), but nothing calls it and
  the value is never stored. `/api/segments` uses `AVG(severe ? 2 : 1)`
  instead. The proper fix is a `risk_score` column written on ingest.
- **`SELECT *` with positional indexes** (`row[7]`, `row[9]`) throughout.
  Adding a column anywhere but the end silently corrupts every response.
  Naming the columns would make this class of bug impossible.
- **Condition filters run in Python after fetching all rows**, rather than in
  SQL. Fine at demo volume, wrong in principle.
- **`@app.on_event("startup")` is deprecated** in current FastAPI. Works, but
  emits warnings; lifespan handlers are the replacement.
- **`WEATHER_LOOKUP` only has `00_*` keys.** Every hour falls through to the
  same default, so weather is effectively constant. Since the PRD wants a
  cached table committed to the repo rather than a live API, this probably
  wants real per-hour entries for the demo clip's date.

## How to verify

```bash
cd server/api
pip install -r requirements.txt
python3 -m uvicorn netra_server:app --port 8000
```

Then from the repo root, in another terminal:

```bash
python3 -m http.server 8080
```

Open `http://localhost:8080/web/`. The badge top-right should read
**"Live API connected"** and the health panel should show your Postgres state.
If it reads "Offline — showing fixtures", the dashboard could not reach the
API and has fallen back to fixtures — check `apiBase` in `web/js/config.js`.
