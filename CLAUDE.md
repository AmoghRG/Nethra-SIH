# CLAUDE.md — NETRA edge pipeline

Working notes for this repo. Read `NETRA-PRD.md` (whole project) and
`NETRA-PRD-edge-pipeline.md` (owners A and B) first — they are the spec, this
file is only the state of play and the traps already hit.

**Scope of the work so far: owners A and B only** — M1 calibration, M2
detection/tracking, M3 conflict engine, M4 gate/benchmark, event emission, plus
the shared scaffolding (`contracts/`, `fixtures/`, `tests/edge/`). C, D, E and
F's directories exist with README stubs and are not ours to fill.

---

## Environment

- Windows 11, Python 3.13.6, PowerShell primary (Bash tool also available).
- Installed and used: `numpy`, `PyYAML`, `requests`, `pytest`, and now
  `ultralytics` 8.4.135, `opencv-python` 5.0.0, `lap` 0.5.13.
- Demo clip: `Road_traffi_ video.mp4` at the repo root. 1920x1080, 30 fps,
  306 s, 9187 frames. Elevated footbridge view over a divided road with a
  merging slip road — the geometry the PRD says the project depends on.
  Gitignored (`*.mp4`): at 170 MB it is over GitHub's 100 MB hard limit.
- **Do not use `pydantic`.** The installed 1.6.2 is paired with a
  `pydantic_core` 2.41.4 and the combination is broken. Everything uses
  dataclasses plus a hand-rolled validator, which also keeps B's lane free of
  dependencies.

Owner B's entire lane and all 70 tests run on numpy alone. `ultralytics` and
`cv2` are **lazy-imported inside functions** so an import of anything under
`edge/` never fails on a machine without them. Keep it that way.

---

## State

Everything below "verified" was actually run in-session with its output
checked. Everything under "unrun" is written but has never executed, because it
needs a demo clip and the detector extras.

### Verified

| Area | Files |
|---|---|
| Contracts | `contracts/*.schema.json`, `api.md`, `README.md` |
| Fixtures | `fixtures/*` (7 data files + README) |
| Shared | `edge/config.yaml`, `edge/common/{config,jsonl,geometry,event}.py` |
| M1 | `edge/calibration/{homography,project}.py` |
| M3 | `edge/conflicts/{ttc,pet,sample,suppression,debounce,engine,run_conflicts}.py` |
| Emission | `edge/emit/{buffer,uploader}.py` |
| One command | `edge/run_pipeline.py` |
| Tests | `tests/edge/*` — **70 passing** |
| Docs | root `README.md`, `fixtures/README.md`, 9 stub READMEs, `labels.csv` header |

### Also verified, on the real clip

`edge/detect/{detector,overlay}.py`, `edge/track/{tracker,run_track,hygiene}.py`,
`edge/gate/motion_gate.py`, `bench/benchmark.py`, `edge/common/threads.py`.

Still unrun: `edge/calibration/{extract_frame,pick_points}.py` (the interactive
picker; the demo-clip calibration was built from measured road features
instead). `edge/detect/classes.py` has no dedicated test.

### Measured on the demo clip

| Acceptance criterion | Result | |
|---|---|---|
| M2: >= 15 FPS at 320x320 on one CPU core | **37.4 FPS**, 1.18 cores, **31.8 FPS/core** | PASS |
| M2: < 5 identity switches / 1,000 frames | **0.67** over 1500 frames, 75 tracks | PASS |
| M1: `rms_error_m` under 0.5 m | **0.136 m**, held out | PASS |
| M7: serialised event <= 400 bytes | **353 bytes** largest | PASS |
| M4: gate cuts detector calls >= 40% | **0.0%** | FAIL, and correctly so |
| M3: >= 1 severe and >= 5 total conflicts | 8 events, 4 severe | PASS |

The gate result is not a bug. It skips frames only when nothing moves, and this
road never goes quiet. On continuous traffic the gate is pure overhead: 37.2
FPS with it against 44.8 without. Report it that way — it is a power and
thermal feature, and this clip cannot demonstrate it. A clip with idle periods
would.

Benchmark, `--threads 1` (`bench/results/benchmark.txt`):

    config       imgsz  gate  threads     FPS  cores  FPS/core   det/min
    gate + 320     320    on        1   37.20   1.42     26.20    2231.9
    plain 320      320   off        1   44.81   1.36     32.95    2688.5
    plain 640      640   off        1   16.45   1.13     14.56     986.7

Full clip end to end: 11,915 track-frames, 238 tracks, 1 dropped for overspeed
(track 91 — the switch screen had independently flagged the same track), 8
events (4 severe, 4 conflict), debounce collapsing 27 readings into 8.

### Measured on fixtures

- Homography: held-out `rms_error_m` **0.0666 m** (budget 0.5).
- Projection: mean position error **0.138 m**, max 0.371.
- TTC: **exactly 1.800 s** at frame 0 of the analytic fixture, 0.720 s at
  frame 27.
- `lane_split.jsonl`: **0 events**; with rule 2 off, 96 readings → 2 severe.
- Sample scene: 2 events (1 severe). Longer scene: 6 events (2 severe).
- Debouncing collapsed 15 readings into 2 events (7.5x).
- Largest real event **352 bytes**; synthetic worst case **356** (limit 400).
- Buffer: 6 events queued with the server down, all 6 drained on reconnect, a
  second replay added nothing.

### Commands

```bash
python -m pytest                        # 70 tests, no detector needed
python -m edge.run_pipeline --dry-run   # full chain on fixtures

python -m edge.calibration.project --tracks fixtures/tracks_px.sample.jsonl \
    --calib fixtures/calibration.json --out out/tracks_m.jsonl
python -m edge.conflicts.run_conflicts --tracks out/tracks_m.jsonl \
    --calib fixtures/calibration.json --out out/events.jsonl --print-events

# rule 2, on and off
python -m edge.conflicts.run_conflicts --tracks fixtures/lane_split.jsonl \
    --calib fixtures/calibration.json
python -m edge.conflicts.run_conflicts --tracks fixtures/lane_split.jsonl \
    --calib fixtures/calibration.json --set suppression.lane_splitting=false
```

---

## The demo clip's calibration, and the one number that is assumed

`fixtures/calibration.demo_clip.json`, built from measured road features:

- **Measured:** four consecutive dashes of the broken centre line. Their
  cross-ratio is 1.3477 against the 4/3 expected for equal world spacing, 1.1%
  off, which confirms they are consecutive and uniform. A straightened strip
  along the centre line (`out/frames/centreline_strip.jpg`) confirms visually
  that there are exactly four, with no faint ones missed.
- **Measured:** the two lane-centre paths, fitted from the ground-contact
  points of 75 real tracks.
- **ASSUMED:** lane width 3.5 m (lateral scale) and dash pitch 6.0 m
  (longitudinal scale).

Both assumptions are unavoidable. A homography absorbs any affine change of
world coordinates, so a metric length along the road cannot be derived from one
across it. Two assumptions in, two scales out.

6.0 m was chosen because it puts the 85th-percentile speed at 52 km/h — the
only candidate in a plausible urban range (4.5 m gives 39, 9.0 m gives 78) —
and because the measured mark-to-gap ratio of roughly 1:2 fits a 2 m mark with
a 4 m gap. **Every distance and speed scales linearly with it.** Replace it
with a satellite measurement between two fixed features before quoting any
figure to a judge.

## Still to do

1. **Ground truth (M9).** 8 events on 5 minutes is plausible, but "plausible"
   is not a measurement. Nothing here has been checked against a human label,
   and the remaining false-positive question (below) cannot be settled without
   one.
2. **Replace the assumed dash pitch** with a satellite measurement.
3. **Hand-check 20 tracks** against the overlay video for identity switches.
   `hygiene.py` screens for positional jumps only and will miss a slow swap
   between adjacent vehicles; the acceptance criterion asks for the
   hand-checked number.
4. **A clip with idle periods** if the motion gate's 40% claim is to be
   demonstrated at all.
5. Optional: a test for `edge/detect/classes.py`.

## Demo artefacts (`demo/`)

Four generators, all driven off the pipeline's own output. See
`demo/README.md` for the commands. `demo/` is owner F's directory; the
deviation is recorded in `contracts/README.md`.

They need a `--trace` file from the conflict engine: an opt-in, off-by-default
per-pair-frame log. With tracing on, TTC is computed even for suppressed pairs
so the plot has a continuous curve and the before/after comparison is measured
rather than inferred. **The engine's decisions are unchanged either way** - the
real clip gives 8 events with and without tracing.

The headline number, from `make_suppression_compare` on the real clip:
**8 events with every rule on, 1379 with them all off** (1037 severe). Rule 2
alone accounts for +45. Two rules remove nothing on this clip
(`speed_sanity`, `validity_region`) because it contains nothing they are for -
worth saying out loud rather than implying the rules are weak.

## The clips, and what each is good for

| clip | use for | why not more |
|---|---|---|
| `video_1` real, 5 min | every metric artefact; `rms_error_m` 0.136 m | scale rests on an assumed 6 m dash pitch |
| `video_5` BeamNG, 6 s, 1080p | detection and tracking on a scripted near-miss: 99% recall, 0 false positives at imgsz 640 / conf 0.25 | cannot be metrically calibrated, see below |
| `video_4` BeamNG, 4.7 s, 480x272 | superseded by video_5 | resolution costs a third of the detections |
| `video_3` real, snowy | nothing | a collision, camera cuts, no markings |
| `video_2` | nothing | a screen recording of a YouTube player |

### Why the BeamNG clip carries no metres

Three approaches failed, all for the same reason: **no ground-plane reference
spans the area where the vehicles are.**

1. Crosswalk only - a genuine verified ruler (cross-ratio 1.3437 against 4/3,
   0.8% off) but confined to a 180x130 px corner patch while the vehicles are a
   median 768 px away. Extrapolation put the horizon through the conflict area;
   implied speeds reached 94,000 km/h.
2. Two vanishing points - needs two world-parallel lines. The junction's roads
   meet at an angle and the only long markings are on different roads.
3. Crosswalk plus a constant-speed vehicle - fitted to rms 3.69 px, but with
   all reference points on just two lines there is a family of solutions
   fitting both cross-ratios and the solver took a degenerate one: every
   validity-polygon corner mapped to world x = 0.

**A known crosswalk pitch would not have fixed this.** The failure is
conditioning, not the unknown constant. What would fix it: two vehicles parked
a measured distance apart mid-junction and a re-export, or the map name.

### Detector settings are per-source, not a default change

`--set detector.imgsz=640 --set detector.conf=0.25` for the sim clip. Measured
over its 149 frames, 119 of which contain a vehicle:

| config | recall | false positives |
|---|---|---|
| imgsz 320, conf 0.35 (the frozen default) | 76% | 20 |
| imgsz 640, conf 0.25 | 99% | 0 |

`edge/config.yaml` is untouched - 320/0.35 is tuned for the edge case, and
`--set` exists for exactly this.

## The open accuracy question

Of the 8 events on the demo clip, roughly 6 still have the two vehicles more
than 2 m apart laterally — a full lane. Those are very probably adjacent-lane
traffic rather than near-misses.

The root cause is structural and worth stating plainly: **two cars are modelled
as circles of radius 2.0 m each, so on a 3.5 m lane they overlap by
construction.** Suppression rule 2 is the designed mitigation and it now
removes the great majority, but the residue needs one of:

- C's M5 lane centrelines, so the lateral gap can be measured against the road
  direction instead of against a vehicle's instantaneous heading (which rotates
  on a bend); or
- smaller radii, or an ellipse — but that changes every TTC in the system.

**Do not tune this against the geometric proxy used during diagnosis.** Per
edge PRD 6.5, thresholds get set on the first half of C's labels and reported
on the second. Tuning against a proxy and reporting against the same proxy is
exactly the trap the PRD warns about, and this clip is European anyway: no
auto-rickshaws, no lane-splitting two-wheelers, so it exercises none of the
adaptation the pitch is built on.

---

## Decisions on the record

Deviations from or additions to the PRDs. Also recorded in
`contracts/README.md`, so they are visible to the whole team.

- **Amendments A1, A2, A3 accepted** (two track files, validity polygon,
  provenance fields).
- **`edge/common/` is jointly owned by A and B.** Not in parent PRD §8. Exists
  because `ConflictEvent` is built in `conflicts/` and shipped from `emit/`,
  and config/JSONL I/O is needed by both owners. Changed by announcement, like
  `edge/config.yaml`. `tests/edge/` likewise.
- **`event_id` is a deterministic content hash** (`evt_<8 hex>` from video_id +
  sorted track pair + min_ttc_frame), not the PRD's `evt_00417` counter. M7
  needs idempotency across restarts and buffer replays; a counter only gives
  uniqueness within one process.
- **`vehicle_*.direction` is omitted entirely by the edge**, not written as
  null. It needs C's lane map, and two explicit nulls cost 38 bytes of the
  400-byte budget — enough to push the worst-case event to 412 and break the M7
  size criterion. D adds it on ingest alongside `conditions`.
- **Config additions** beyond edge PRD §3, all below a marked divider in
  `config.yaml`: `suppression` (the six toggles, required by §6.4),
  `lane_split`, `motion`, `video`, `emit`, `gate`, `conflicts.pet_*`.
- **Homography solved with a pure-numpy DLT**, not `cv2.findHomography`. Keeps
  B's whole critical path free of OpenCV. Hartley normalisation included.
- **A2's polygon is applied to the tracker's output**, not its input.
  Ultralytics' `model.track()` exposes no seam between detection and
  association. Same effect downstream; documented in `run_track.py` rather than
  implied otherwise.
- **`fixtures/events.edge.sample.json`** is ours (edge-shaped). F's
  `fixtures/events.sample.json` (8 events, enriched) is still F's to commit —
  do not create it.

---

## Traps already hit — do not re-learn these

**TTC case ordering.** The PRD's case table lists "smallest root < 0 →
diverging" before "C < 0 → overlapping". Implemented in that literal order it is
a bug: when `C < 0` the roots always straddle zero, so every overlapping pair
gets discarded as diverging. **Overlap must be tested first.** Guarded by
`test_overlapping_is_checked_before_the_root_sign`.

**Suppression rule 2 discriminator.** The first attempt tested longitudinal
separation ("abreast, not behind") and misfired: a motorcycle filtering from
15 m back is still filtering. The correct discriminator is the **lateral gap** —
parallel headings, gap stable (`lateral_stability_m`), gap at least
`min_lateral_offset_m`. That suppresses filtering at any following distance
while preserving genuine rear-end conflicts (same path, gap ≈ 0).

**PET needs a second pass.** Computing it when the debouncer closes an
encounter returns null for exactly the crossing conflicts it is meant to
measure, because the second vehicle often reaches the crossing *after* the
encounter closed. The engine sweeps, collects encounters, then builds events.
PET is also bounded to crossings within `pet_max_s` of the encounter — without
that, an unrelated crossing elsewhere in the clip gets attached and looks
plausible.

**Fixture near-misses need someone to brake.** Two vehicles at constant
velocity on a converging course whose miss distance is under the sum of the
circle radii simply overlap, and the engine correctly but uselessly reports
TTC 0. Every synthetic crossing has the second vehicle braking.

**An ID switch is a step, not an alternation.** A test that jitters a box back
and forth will not trip the overspeed guard, and *should* not — the
least-squares window averages it away, which is its job. A real switch is a
persistent jump. Both directions are now tested.

**`heading_deg` must be re-wrapped after rounding.** `round(359.97, 1)` is
360.0, which is outside the contract's `[0, 360)`. Caught by the contract test
against the fixture.

**The 400-byte budget is tight.** Realistic events land at 350–390. Before
`direction` was dropped, the worst case was 412. Any new field needs the
worst-case test re-run, not just a spot check on a sample event.

**Non-ASCII in printed strings breaks the Windows console.** Em-dashes rendered
as `?` in the run summaries. Docstrings are fine (never printed); **any string
literal that can reach stdout must be ASCII.**

**Heredocs mangle backslashes in this shell.** Writing Python via
`cat <<'EOF'` in the Bash tool corrupted `\n` inside string literals and failed
outright on some content. **Use the Write tool for source files**; keep Bash for
verification and inspection. For a long generated document, write a script file
and execute it rather than piping a heredoc.

**`pytest.ini` disables the `langsmith_plugin`.** The broken pydantic install
makes that auto-loaded plugin explode at collection time, before a single test
runs. Do not "fix" it by adding pydantic to `requirements.txt`.

**Ultralytics resets `torch.set_num_threads` during inference.** Setting it
before the run is not enough — the first `predict`/`track` call puts it back to
8, silently. `Tracker.update` and `Detector.detect` therefore call
`edge.common.threads.repin()` afterwards. Without it every FPS figure is of
something other than what was asked for.

**Unrestricted threading is the SLOWEST configuration.** At 320x320 on 20
logical cores: 48.6 FPS pinned to 1 thread against 32.1 FPS unpinned, and 18
cores busy to do it. The model is small enough that oversubscription costs more
than it buys. `--threads 1` is both the fastest setting and the one the
per-core acceptance criterion asks for, so it is the default in
`run_pipeline` and `bench.benchmark`.

**`check()` returns the FIRST rule that fires.** A test asserting
`check(...) != RULE_LANE_SPLITTING` can pass because rule 1 fired first,
without rule 2 ever being reached. Assert rule-2 behaviour against
`_lane_splitting` directly.

**The debouncer opens an encounter on the first frame a pair is seen** and then
locks in the minimum TTC it ever observes. Any suppression rule that waits for
a history window before it may fire therefore never gets to speak — the
encounter is already open and its minimum already recorded. Removing that wait
from rule 2 took the demo clip from 34 events to 8.

**A closing-RATE test is worse than a variance test here.** Fitting a slope to
the lateral gap sounded better reasoned and measured four times worse (34
events against 8): the road bends, the reference heading rotates, and a slope
over half a second reads that rotation as 1-2 m/s of convergence that is not
happening. Spread over the same window stays well inside threshold. The
variance test is also insensitive to its own threshold (0.75, 1.5 and 3.0 m all
give 8 events), so it is not a knife edge.

**Recall without a false-positive count is a misleading number.** On the first
BeamNG cut, dropping confidence to 0.20 took recall to 98% - by inventing
vehicles in 19 empty frames. 30 of that clip's 112 frames contain no vehicle at
all, so "fewer empty frames" looked like better detection and was the opposite.
Always separate genuinely-empty frames from detector misses before quoting a
recall.

**A stationary-track filter must measure a RATE, not a displacement.** The
first version of `drop_stationary_tracks` compared total movement to box size
and would have deleted a real vehicle glimpsed for half a second along with the
treeline artefact it was aimed at. Box-lengths per second separates them
cleanly. Guarded by `tests/edge/test_hygiene.py`.

**`pick_event` skips a TTC of exactly zero.** Zero is the already-overlapping
case that `ttc.py` flags as suspicious; it has no approach to plot and is the
worst thing to put in front of a judge. The demo scripts pick the lowest
non-zero TTC instead.

**Far-field speed noise is real and expected.** With the PRD's 7-frame window,
speed error on the sample fixture is unbiased but its spread grows with depth:
~1.1 km/h σ near, ~4.1 km/h at 25 m. That is the far-field geometry amendment
A2 exists for, not a smoothing bug. If TTC looks jumpy, raise
`geometry.smooth_window` or tighten `max_range_m` before suspecting the tracker.

---

## Conventions

- **SI everywhere internally.** Metres, seconds, m/s. km/h only at JSON
  serialisation, via `geometry.mps_to_kmh`.
- **Ground frame:** origin at the first calibration reference point, X east,
  Y north, right-handed. `heading_deg` is a compass bearing, 0 = +Y, clockwise,
  range `[0, 360)`.
- **`t` is seconds from video start.** Wall-clock is derived only at emission
  from `video.start_time`. Never mix them.
- **Bottom-centre of the bbox** is the ground contact point, never the centroid.
- **No tuned constant is hardcoded.** It lives in `edge/config.yaml` or it is a
  bug. `--set key=value` overrides at the CLI.
- **`severity` is derived from `ttc_s`** and has no setter. **No blame or fault
  field, ever.**
- A failing event is **logged and dropped, never partially written.**
- Fixtures are **append-only**. A new case gets a new file; never edit one in
  place. The generator lives in the scratchpad, not the repo.

## Ownership

`edge/calibration/`, `edge/conflicts/`, `edge/emit/` are **B**.
`edge/detect/`, `edge/track/`, `edge/gate/`, `bench/` are **A**.
`edge/common/`, `edge/config.yaml`, `tests/edge/` are **joint A+B**.
`contracts/` and `fixtures/` are **F's**, with A and B owning the specific
fixture files listed in edge PRD §10. Everything under `server/`, `web/`,
`eval/`, `demo/`, `edge/norms/` belongs to other owners — do not put logic
there.
