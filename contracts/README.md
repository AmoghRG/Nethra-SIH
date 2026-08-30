# contracts/ — frozen interface schemas

**Owner: F (integration lead). Frozen at hour 2.**

After hour 2, changing anything in this directory requires F's sign-off and a
broadcast to the whole team. Every module codes against these schemas, not
against another owner's implementation. That is the mechanism that lets six
people build in parallel — see parent PRD §5 and §6.

| File | Describes | Produced by | Consumed by |
|---|---|---|---|
| `calibration.schema.json` | `calibration.json` | B (M1) | B (projection), A (validity polygon) |
| `tracks_px.schema.json` | `tracks_px.jsonl` | A (M2) | B (M1 projection) |
| `tracks_m.schema.json` | `tracks_m.jsonl` | B (M1 projection) | B (M3), C (M5 norms), C (M10 baseline) |
| `conflict_event.schema.json` | `ConflictEvent` | B (M3) | D (M7 ingest), D (M6 enrich), E (M11), F (M8) |
| `norms.schema.json` | `norms.json` | C (M5) | E (M11), D (scoring) |
| `api.md` | HTTP surface | D (M7) | E (M11), B (M7 emit), F (M8) |

---

## Amendments accepted at hour 0

The edge PRD (§2) raised three amendments to the parent PRD's frozen schemas.
**All three are accepted** and are reflected in the schema files here.

### A1 — `tracks.jsonl` is split into two files

The parent contract had one track file carrying both A's pixel data and B's
ground-plane projection. Two owners writing one file is exactly the merge
conflict the ownership structure exists to prevent.

- `tracks_px.jsonl` — **A produces.** Pixels only.
- `tracks_m.jsonl` — **B produces**, consuming the above.

A and B now share zero source files. The cost is one extra read of a text
file, which at this data volume is milliseconds.

### A2 — `calibration.json` gains a validity region

```json
"valid_region_px": [[120,400],[1180,400],[1240,700],[60,700]],
"max_range_m": 45.0
```

Homography error grows sharply with distance from the camera; near the
vanishing point a one-pixel error can mean tens of metres. Any detection whose
ground-contact point falls outside the polygon is discarded before tracking.
This one field removes a whole category of false positive.

### A3 — `ConflictEvent` gains provenance fields

```json
"track_ids": [87, 92],
"t_video_s": 41.33,
"min_ttc_frame": 1247
```

`t_video_s` lets C line an event up against a hand-written label without
guessing. Without it, the M9 ground-truth comparison becomes manual and slow.

---

## Two further decisions recorded here

These are not schema changes, but they are cross-owner decisions and belong on
the record rather than buried in a source file.

### `edge/common/` is jointly owned by A and B

Parent PRD §8 does not list it. It exists because `ConflictEvent` is
constructed in `edge/conflicts/` and serialised in `edge/emit/` — both B's —
while config loading and JSONL I/O are needed by A and B alike. It is
**jointly owned, changed by announcement**, exactly as `edge/config.yaml`
already is under edge PRD §3. `tests/edge/` is likewise joint A+B.

### `demo/` carries the artefact scripts, though it is F's directory

Parent PRD 8 assigns `demo/` to owner F, and the working rule is that nobody
writes logic in another owner's directory. The demo artefact generators
(`make_overlay.py`, `make_ttc_plot.py`, `make_panel.py`,
`make_suppression_compare.py`, `_common.py`) were written by A and B and live
there anyway, because demo-script material has no other natural home and the
alternative was a new top-level directory nobody owns.

They are presentation scripts, not pipeline code: nothing under `edge/` imports
them, and deleting the whole directory would not affect the edge pipeline or
its tests. Recorded here rather than left silent, on the same basis as
`edge/common/` above.

### `event_id` is a deterministic content hash, not a counter

Format `evt_<8 hex>`, derived from `video_id`, the unordered track pair, and
`min_ttc_frame`. The parent PRD's example shows `evt_00417`; a counter is not
stable across re-runs or partial replays, and M7 requires ingest to be
**idempotent on `event_id`**. A deterministic id makes that idempotency hold
across process restarts and buffer replays, not merely within one run.

---

## Fields the edge deliberately leaves null

Edge PRD §1: *we never write a field we do not compute ourselves.*

| Field | Left null by the edge | Populated by |
|---|---|---|
| `conditions` | Weather, light and surface are server-side (M6) | D |
| `vehicle_a.direction`, `vehicle_b.direction` | **Omitted entirely**, not written as null. Needs C's learned lane directions (M5); the edge has no lane map. Two explicit nulls cost 38 bytes of the 400-byte budget and pushed the worst-case event to 412 | C's norms, applied by D |
| `pet_s` | Null when PET is not computed or no crossing point exists | — (optional by design) |

If `conditions` arrives at the server already populated, that is a bug in the
edge, not a feature.
