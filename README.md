# NETRA

**Near-miss Enabled Traffic Risk Analytics** — SIH 2026 Grand Finale.

Directory layout follows PRD Section 8. **Only a directory's owner edits it**;
a change anywhere else goes through that owner. `contracts/` requires
integration-owner sign-off.

```
netra/
├── contracts/            # F — frozen at hour 2
├── fixtures/             # F — append-only
├── edge/
│   ├── calibration/      # B   M1
│   ├── detect/           # A   M2
│   ├── track/            # A   M2
│   ├── gate/             # A   M4
│   ├── conflicts/        # B   M3
│   ├── norms/            # C   M5
│   └── emit/             # B   M7
├── server/
│   ├── api/              # D   M7   <- built
│   ├── enrich/           # D   M6   (currently inside api/netra_server.py)
│   ├── scoring/          # D        (currently inside api/netra_server.py)
│   └── narrate/          # F   M8
├── web/                  # E   M11  <- built
├── eval/
│   ├── groundtruth/      # C   M9
│   └── baseline/         # C   M10
├── bench/                # A   M4
├── demo/                 # F
└── tools/                # shared build scripts
```

## Status

| Lane | Owner | State |
|---|---|---|
| M11 dashboard | E | Built, tested against the live API |
| M6/M7 server | D | Built; 9 bugs patched — see `server/PATCHES_TO_OWNER_D.md` |
| M1–M5, M8–M10 | A, B, C, F | Not started — placeholder READMEs only |

`server/enrich/` and `server/scoring/` are empty because D's M6 and scoring
code currently lives inside `server/api/netra_server.py`. Splitting it is D's
call; leaving it whole keeps the merge clean.

## Running it

**1. Server** (owner D)

```bash
cd server/api
pip install -r requirements.txt
python3 -m uvicorn netra_server:app --port 8000
```

Needs Postgres with a `netra` database and a `netra_user` / `netra_pass` login.
PostGIS is optional. Without Postgres the server still starts and buffers to
SQLite, but `/api/events` returns `ok: false`.

**2. Road geometry** — once, needs internet

```bash
python3 tools/fetch_roads.py
python3 tools/gen_fixtures.py
```

On Windows use `py -3.14` (or whichever interpreter has `certifi`); the msys2
Python usually has no CA bundle and every HTTPS call fails.

**3. Dashboard** (owner E)

```bash
python3 -m http.server 8080      # from the repo root, not from web/
```

Open `http://localhost:8080/web/`.

Or open `demo/netra-dashboard-standalone.html` directly — one self-contained
file, no server, everything inlined. Rebuild it with
`python3 tools/build_standalone.py` after changing anything in `web/`.

## Configuration

Everything site-specific is in `web/js/config.js`:

- `junction` — the site under analysis. Currently **Pumpwell Circle,
  Mangaluru** (12.86889, 74.86389). This is a placeholder until PRD Q1 (which
  demo video) is settled. Change it, re-run `tools/fetch_roads.py`, and
  corridors, context roads and event placement all follow.
- `apiBase` — where D's server is. Default `http://localhost:8000`.
- `carto.key` — basemap key from <https://carto.com/basemaps/apikey> (free, no
  account). **Not** the CARTO Platform token: that one authenticates
  `carto_dw` data sources and will not serve basemap tiles.

## Known integration gaps

- `/api/segments` returns scored **points**, not road geometry, so the map
  takes corridors from OpenStreetMap and computes risk from the event list.
  See `contracts/http_api.md`.
- Clip delivery has no contract. `web/` expects `event.clip` as a relative
  path; nothing serves it yet, and M7's design keeps video on the edge.
- `detection_quality` is displayed but not yet used to normalise conflict
  rates, which M6 calls for.
