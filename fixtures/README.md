# fixtures/ — hand-committed sample data

**Fixtures are append-only. Nobody edits an existing fixture; a new case gets a
new file.** If a fixture is wrong, that is worth knowing loudly — silently
changing one under a passing test is how a regression test stops being one.

The point of this directory is parent PRD §6: **no module may block on another
module's output.** Owner B built the entire conflict engine against
`tracks_px.sample.jsonl` before owner A's detector existed. Owners C, D, E and
F can start against `events.edge.sample.json` before a demo clip has even been
chosen.

The generator was deliberately **not** kept in the repo. These are committed
artefacts, not build output, and having a regenerate script in the tree invites
someone to re-run it at hour 20 and quietly move the numbers a test depends on.

---

## Files owned by A and B

| File | What it is | Regression test for |
|---|---|---|
| `calibration.json` | Plausible homography for a 1280×720 elevated camera over a 24 × 40 m patch, with amendment A2's validity polygon and a **held-out** reference point | M1 |
| `tracks_px.sample.jsonl` | 567 rows, 9 s, 4 tracks. Two vehicles converging, plus a follower and a bus passing clear. Pixels only (amendment A1) | M2 output shape |
| `tracks_m.sample.jsonl` | The above through `edge/calibration/project.py` | M1 projection |
| `tracks_px.scene.jsonl` | 1925 rows, 33 s, 12 tracks, in three acts. The source of `events.edge.sample.json` | — |
| `synthetic_collision.jsonl` | Two cars head-on with an **analytically computed** TTC | `edge/conflicts/ttc.py` |
| `lane_split.jsonl` | A motorcycle filtering between two cars | Suppression rule 2 |
| `events.edge.sample.json` | 6 real engine events over the longer scene | Downstream owners |

### The two that matter most

**`synthetic_collision.jsonl`** catches maths errors. Two cars, 40 m apart,
10 m/s each, radii 2.0 + 2.0. The header carries the full derivation:
`TTC(frame 0) = 1.800 s` and `TTC(frame 27) = 0.720 s`, both exact. If either
number moves, the quadratic is wrong. Nothing about the road changed.

**`lane_split.jsonl`** catches the false-positive class that would otherwise
flood the output. **Expected result: zero events.** Try it both ways:

```bash
python -m edge.conflicts.run_conflicts --tracks fixtures/lane_split.jsonl \
    --calib fixtures/calibration.json
# -> 0 events, 212 pair-frames removed by rule 2

python -m edge.conflicts.run_conflicts --tracks fixtures/lane_split.jsonl \
    --calib fixtures/calibration.json --set suppression.lane_splitting=false
# -> 96 conflict readings, 2 severe events, all of them nonsense
```

That contrast is a demo beat, not just a test.

---

## Things to know before trusting these numbers

**They are synthetic.** `calibration.json` carries
`method.technique: "vehicle_length"` and a note saying so. Replace it with the
real measurement for the demo clip before quoting any figure from it.

**Each crossing conflict is specified by the minimum TTC it should produce**,
not by a magic timestamp. The scene generator searched for the entry time and
braking profile that lands on a stated target, so the severity that comes out
can be justified rather than merely asserted.

**Every near-miss involves someone braking.** Two vehicles held at constant
velocity on a converging course whose closest approach is under the sum of the
circle radii simply overlap, and the engine then correctly but uselessly
reports TTC 0. A real near-miss has someone react, and so do these.

**Bounding boxes carry ±1.5 px of jitter**, so the least-squares velocity fit
has something real to smooth. That jitter is why the speed numbers are not
perfectly flat. The error is **unbiased but its spread grows with depth**:
about 1.1 km/h σ in the near field, about 4.1 km/h at 25 m out. That is the
far-field geometry amendment A2 exists for, not a fault in the smoothing. If
TTC looks jumpy on far-field tracks, raise `geometry.smooth_window` or tighten
`max_range_m` before suspecting the tracker.

---

## Fixtures still to come, owned by others

Not ours to write. Listed so nobody assumes they are missing by accident.

| File | Owner | Purpose |
|---|---|---|
| `events.sample.json` | F | 8 events **enriched with `conditions`**, for E's dashboard and F's narration. `events.edge.sample.json` is the edge-shaped input to it: same records with `conditions` null and no `direction`, exactly as the edge emits them |
| `norms.json` | C | 85th-percentile speed, lane clusters |
| `api/*.json` | F / D | Canned endpoint responses for E's fixture server |
