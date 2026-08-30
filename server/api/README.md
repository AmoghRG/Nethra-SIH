# server/api/ - M7 event ingest and query

**Owner: D. Priority P0.** Nothing here yet.

## Contract

`contracts/api.md` and `contracts/conflict_event.schema.json`. Both are frozen;
changing either needs F's sign-off.

    POST /api/events                                ingest one or many
    GET  /api/events?from=&to=&light=&weather=      filtered list
    GET  /api/segments                              scored road segments
    GET  /api/health                                pipeline status

Every response uses the envelope `{"ok": true, "data": {}, "error": null}`.

## What the edge already assumes of you

`edge/emit/uploader.py` is written and tested against these three assumptions.
If one has to change, that is a contract change.

- The body of `POST /api/events` is a **JSON array** of ConflictEvents. The
  edge batches; it never sends a bare single object.
- Ingest is **idempotent on `event_id`**. The edge buffers to SQLite on any
  failure and replays blindly on reconnect, so an ambiguous failure results in
  a re-send rather than an attempt to work out what you received.
- Any 2xx means accepted and durable. Anything else, including a timeout, means
  not accepted, and those events stay queued.

## Input available now

`fixtures/events.edge.sample.json` - 6 real events exactly as the edge emits
them: `conditions` null, no `direction`. Both are yours to populate.

    python -m edge.emit.uploader --events fixtures/events.edge.sample.json --target http://localhost:8000/api/events

## First file

`server/api/main.py` - FastAPI or Flask, `POST /api/events` with an upsert on
`event_id`. Postgres with PostGIS; **SQLite is an acceptable substitute if
Postgres setup exceeds 45 minutes**, since the demo does not depend on spatial
indexing at this data volume.

## The buffer demo depends on you

Killing this server mid-run, letting events accumulate on the edge, and
restarting it to watch them replay is a rehearsed fifteen seconds of the demo.
It only works if ingest is genuinely idempotent.
