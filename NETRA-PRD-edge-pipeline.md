# NETRA — Edge Pipeline PRD

**Owners A and B · Modules M1, M2, M3, M4 and event emission**
Sub-document of the NETRA PRD v1.0 · 24-hour Grand Finale sprint

| Field | Value |
|---|---|
| Scope | Video file in → validated ConflictEvent records out |
| Owners | **A** (vision) and **B** (geometry and conflicts) |
| Hardware | None. Laptop CPU, recorded footage only |
| Consumes | A demo video, and nothing else from any other owner |
| Produces | `events.jsonl`, and `POST /api/events` to D's server |
| Status | Draft — three contract amendments require sign-off at hour 2 |

---

## 0. Why this pair is one document

A and B together own an unbroken chain. Nothing between the video file and the event record belongs to anyone else, and nothing in that chain can be tested without the rest of it.

They also share the sprint's only genuinely hard technical risk: **a conflict detector that fires constantly is worse than no conflict detector at all.** Section 6 is dedicated to that, and it is the part of this document to read twice.

Everyone else consumes what this pair produces. If this chain does not work by Gate 1, the demo has no content.

**Standing assumption:** greenfield. If detection, tracking, or homography code already exists from the internal round, tell the team lead — most of Section 8's schedule collapses and M4 becomes viable.

---

## 1. Scope boundary

### Owned by A and B

- Camera calibration and the pixel → metre transform
- Vehicle detection and multi-object tracking
- Ground-plane trajectory construction and velocity estimation
- TTC and PET computation, conflict classification
- False-positive suppression and event debouncing
- Event serialisation, local buffering, and upload
- Motion gating and the two-tier benchmark harness

### Explicitly *not* owned

| Not ours | Owner |
|---|---|
| Weather, light, and surface tagging | D — the edge writes `conditions: null` |
| Exposure normalisation and risk scoring | D |
| Self-calibrating norms (85th-percentile speed, lanes) | C — consumes our tracks, we do not build it |
| Ground-truth labelling | C |
| Any narration or prose | F |
| Anything with a user interface | E |

**One rule that prevents most cross-owner friction:** we never write a field we do not compute ourselves. `conditions` stays null leaving the edge. If it arrives at the server populated, that is a bug.

---

## 2. Contract amendments — raise these at hour 2

The parent PRD froze five schemas. Three need adjusting before this pipeline can be built cleanly. Each needs F's sign-off before hour 2, after which they are frozen like everything else.

### A1 — Split `tracks.jsonl` into two files

**Problem.** The parent contract has one track file carrying both pixel data (A's output) and ground-plane data (B's projection). Two owners writing one file is exactly the merge conflict this structure exists to prevent.

**Proposed.** Two files, one hard seam:

- `tracks_px.jsonl` — **A produces.** Pixels only: `frame`, `t`, `track_id`, `cls`, `bbox`, `conf`
- `tracks_m.jsonl` — **B produces**, consuming the above. Adds `ground_m`, `v_mps`, `speed_kmh`, `heading_deg`

**Why this is worth an extra pass over the data.** A and B now share zero source files. A can rewrite the tracker at hour 10 without touching anything B owns, and B can develop the entire conflict engine against a fixture track file before A's detector produces a single frame. The cost is one extra read of a text file; at our data volume that is milliseconds. Fuse the stages after the finale if you ever need to.

### A2 — Add a validity region to `calibration.json`

**Problem.** Homography error grows sharply with distance from the camera. Near the vanishing point, a one-pixel error can mean tens of metres. Conflicts "detected" there are noise.

**Proposed.** Add:

```json
"valid_region_px": [[120, 400], [1180, 400], [1240, 700], [60, 700]],
"max_range_m": 45.0
```

Any detection whose ground-contact point falls outside the polygon is discarded before tracking. This single field removes a whole category of false positive.

### A3 — Add provenance fields to `ConflictEvent`

**Proposed additions**, needed for C's validation work and for defending numbers to a judge:

```json
"track_ids": [87, 92],
"t_video_s": 41.33,
"min_ttc_frame": 1247
```

`t_video_s` lets C line an event up against a hand-written label without guessing. Without it, the M9 comparison becomes manual and slow.

---

## 3. Conventions — agree these once, then never discuss again

**Units.** All internal computation is SI: metres, seconds, metres per second. Conversion to km/h happens *only* at JSON serialisation. Mixed units are the most common silent bug in this kind of pipeline, and they produce plausible-looking wrong answers rather than crashes.

**Ground coordinate frame.** Origin at the first calibration reference point. X east, Y north, both in metres, right-handed. Fixed for the whole sprint.

**Time.** `t` is seconds from the start of the video, as a float. Wall-clock timestamps are attached at emission, derived from a configured video start time. Never mix the two.

**The ground contact point.** This one matters more than it looks. When projecting a bounding box to the ground plane, use the **bottom-centre of the box**, not the centroid.

The homography maps the *road surface*. A vehicle's centroid floats above that surface, so projecting it places a bus several metres from where it actually is, and the error scales with vehicle height. Projecting the bottom-centre — where the tyres meet the road — is the only correct choice.

This is the single most common calibration bug in traffic-vision projects, and it produces distances that look reasonable while being systematically wrong.

**Configuration.** No tuned constant is hardcoded. All live in `edge/config.yaml`, owned jointly, changed by announcement.

```yaml
detector:
  weights: yolov8n.pt
  imgsz: 320
  conf: 0.35
  iou: 0.45
  classes: [car, motorcycle, truck, bus, auto, person]
tracker:
  track_thresh: 0.5
  match_thresh: 0.8
  track_buffer: 30
  min_track_frames: 5
geometry:
  smooth_window: 7
  max_speed_kmh: 150
conflicts:
  ttc_conflict_s: 1.5
  ttc_severe_s: 0.8
  pet_threshold_s: 1.5
  min_closing_speed_mps: 2.0
  parallel_heading_deg: 20
  debounce_s: 3.0
radii_m:
  motorcycle: 1.0
  auto: 1.4
  car: 2.0
  truck: 3.5
  bus: 3.5
  person: 0.4
```

---

## 4. M1 — Calibration and ground projection · Owner B

### 4.1 The problem

A camera produces a flat image with no depth. Two vehicles that appear equally far apart on screen may be 2 metres or 20 metres apart in reality, depending entirely on where they sit in the frame. Every downstream number — speed, distance, time-to-collision — is meaningless until this is fixed.

A homography is a 3×3 matrix mapping the image plane to the road plane. It works because the road is (approximately) flat, so one matrix describes the whole surface.

### 4.2 Getting real distances without visiting the site

This is your actual constraint: no tape measure, no site access, only footage. Four options, best first.

| Method | Typical accuracy | Notes |
|---|---|---|
| **Satellite imagery** | Best available to you | If the junction is identifiable, measure real distances between fixed features on a mapping tool. Kerb corners and pole bases work well; anything that moves does not |
| **Lane width** | Moderate | Indian urban lanes typically run 3.0–3.5 m. **Verify against IRC guidance rather than taking this number from me** — it varies by road class, and the error propagates into every speed you report |
| **Vehicle length** | Moderate, improves with averaging | A common hatchback is roughly 3.8–4.0 m, an auto-rickshaw roughly 2.6 m, a motorcycle roughly 2.0 m. Measure across ten stationary vehicles and average |
| **Road markings** | Situational | Zebra stripe pitch or dash length, *if* the markings follow a standard you can confirm for that road |

Whichever you use, **record the method and its assumed uncertainty in `calibration.json`**. A judge asking "how do you know that's 3.5 metres?" gets a real answer instead of a shrug — and that exchange is one of the more likely questions you will face.

### 4.3 Error budget — know this before you are asked

Calibration scale error propagates linearly and unforgivingly:

- 10% scale error → 10% speed error → roughly 10% TTC error
- A conflict at a true TTC of 0.85 s is reported at 0.77 s and misclassified as **severe**

So a 10% calibration error moves events across your severity threshold. It does not merely add noise; it changes your headline counts.

**Mitigation:** hold out one measured distance from the fit, re-project it, and report the residual as `rms_error_m`. If it exceeds 0.5 m, re-pick points before building anything on top.

### 4.4 Implementation

1. Extract one frame from the demo video
2. Click four or more coplanar, non-collinear points on the road surface — spread them wide, and prefer the near and middle field over the far field
3. Enter each point's ground coordinate in metres
4. `cv2.findHomography` with RANSAC if more than four points, `cv2.getPerspectiveTransform` for exactly four
5. Validate against a held-out distance
6. Draw the validity polygon (amendment A2)
7. Write `calibration.json`

**Point selection guidance.** Four points at the corners of a wide quadrilateral beat eight points bunched in the middle. Avoid anything near the horizon — those points dominate the fit and are where the projection is least trustworthy.

### 4.5 The projection stage

`edge/calibration/project.py` reads `tracks_px.jsonl` and writes `tracks_m.jsonl`.

For each detection: take bottom-centre of the bbox, reject if outside the validity polygon, apply the homography, and store `ground_m`.

**Velocity estimation — do not skip this.** Frame-to-frame differencing of positions is far too noisy to use directly. Detection jitter of two or three pixels becomes several km/h of phantom velocity, and phantom velocity is the leading cause of phantom conflicts.

Fit velocity by **least squares over a sliding window** of `smooth_window` frames (default 7), or use a Savitzky-Golay filter. Either is a few lines. Neither is optional.

Discard any track whose smoothed speed exceeds `max_speed_kmh`. On urban footage that indicates an identity switch, not a fast vehicle.

### 4.6 Acceptance criteria

- Held-out reference distance re-projects within **10%** of its known value
- `rms_error_m` computed, stored, and under 0.5 m
- A vehicle tracked across the frame produces a smoothly varying speed, not a sawtooth — verified by plotting one track
- Bottom-centre projection confirmed by overlaying projected positions on the frame: markers sit at the tyres, not the roofline
- Runs over a 10-minute track file in under 30 seconds

**Cut line.** If the interactive picker is not working by hour 5, hardcode the matrix for the one demo clip. The demo needs correct numbers, not a reusable tool.

**Owns:** `edge/calibration/`

---

## 5. M2 — Detection and tracking · Owner A

### 5.1 Purpose

Turn a video into continuous vehicle paths. A single frame carries no information about danger; only paths do.

**We train nothing.** YOLOv8n and ByteTrack are used exactly as shipped. Any hour spent fine-tuning is an hour stolen from the conflict engine, and a fine-tuned model is harder to defend than an off-the-shelf one.

### 5.2 Pipeline

1. Decode frames with OpenCV
2. YOLOv8n at 320×320, confidence 0.35
3. Map COCO classes to ours — note COCO has no auto-rickshaw class, so autos will surface as `car` or `truck`. **State this limitation rather than hiding it**; on Indian roads it is the most visible weakness of an off-the-shelf detector, and a judge who knows the domain will spot it
4. Reject detections outside the validity polygon
5. ByteTrack for identity association
6. Drop tracks shorter than `min_track_frames`
7. Write `tracks_px.jsonl`

### 5.3 Why identity switches matter more here than in most projects

In an ordinary detection demo, an ID switch is cosmetic. Here it is catastrophic.

When track 87 switches to a vehicle 15 metres away, the smoothed velocity registers an enormous jump. The conflict engine sees a vehicle apparently travelling at 200 km/h toward another, and emits a severe conflict that never happened. **One ID switch can manufacture one false severe event** — and severe events are your headline number.

Hence the speed sanity check in Section 4.5, and hence measuring the switch rate rather than assuming it.

### 5.4 Acceptance criteria

- Fewer than **5 identity switches per 1,000 frames**, measured by spot-checking 20 tracks against an overlay video
- Sustains **≥15 FPS** at 320×320 on one laptop CPU core, measured and recorded
- Only the six configured classes appear in output
- No track shorter than 5 frames in the output file
- Overlay video renders with boxes and IDs — this is both a debugging tool and demo material, so build it early

**Cut line.** Accept a worse switch rate and report it honestly in the accuracy table. Do **not** change trackers mid-sprint; the integration cost will exceed the benefit every time.

**Owns:** `edge/detect/`, `edge/track/`

---

## 6. M3 — Conflict engine · Owner B

**This is the module the project lives or dies on. Budget accordingly.**

### 6.1 TTC — the computation

Model each vehicle as a circle on the ground plane, radius by class from config. Assume constant velocity over the prediction horizon.

For vehicles *a* and *b*, let `Δp = p_a − p_b` and `Δv = v_a − v_b`, with `R = r_a + r_b`.

The circles touch when `|Δp + Δv·t| = R`, which expands to a quadratic in *t*:

```
|Δv|²·t²  +  2(Δp·Δv)·t  +  (|Δp|² − R²)  =  0
```

Solve with `A = |Δv|²`, `B = 2(Δp·Δv)`, `C = |Δp|² − R²`:

| Condition | Meaning | Return |
|---|---|---|
| `A ≈ 0` | Parallel, no relative motion | TTC = ∞ |
| `B² − 4AC < 0` | Paths never come within R | TTC = ∞ |
| Smallest root `t < 0` | Encounter already past, diverging | TTC = ∞ |
| `C < 0` | Circles already overlapping | TTC = 0 — **suspicious**, flag rather than trust |
| Otherwise | `t = (−B − √(B²−4AC)) / 2A` | TTC = t |

Classify: `TTC < 0.8 s` severe, `TTC < 1.5 s` conflict.

The radii are a simplification — vehicles are rectangles, not circles. Say so if asked. Rectangle intersection is more accurate and considerably more code, and it is not where your 24 hours should go.

### 6.2 PET — the second measure

Where TTC is predictive, PET is retrospective. Find where two ground-plane paths cross, then measure the gap between the first vehicle clearing that point and the second arriving.

`PET = t_arrival(B) − t_departure(A)`

PET catches encounters TTC misses — particularly two vehicles that never were on a true collision course but passed through the same space uncomfortably close in time.

**Cut line:** PET goes first. TTC alone is a complete and defensible story.

### 6.3 False positives — the section to read twice

A naive TTC implementation on Indian urban footage will emit hundreds of conflicts per minute, and every one of them will be garbage. Here is why, and what to do.

| # | Source | Why it fires | Suppression |
|---|---|---|---|
| 1 | **Velocity noise** | Jitter creates a phantom closing component | Smoothed velocity (4.5); require `\|Δv\| > min_closing_speed_mps` |
| 2 | **Lane-splitting two-wheelers** | Motorcycles filtering between cars are genuinely close — this is *normal traffic*, not a near-miss | Exclude pairs with heading difference < `parallel_heading_deg` **and** stable lateral separation |
| 3 | **Queued traffic at a signal** | Stopped vehicles have tiny `Δp`; `C < 0` yields TTC = 0 | Require both speeds above ~1.5 m/s |
| 4 | **ID switches** | Teleporting track implies huge velocity | Speed sanity check (4.5) |
| 5 | **Far-field geometry** | Metre error explodes near the horizon | Validity polygon (A2) |
| 6 | **No debouncing** | At 25 FPS, one 2-second encounter emits ~50 events | See below |

**Source 2 deserves its own paragraph.** Lane discipline on Indian roads is not what surrogate-safety literature from Sweden assumes. Motorcycles filtering between slow cars produce TTC values that look alarming and represent entirely routine behaviour. If you do not suppress this class, your event stream will be almost entirely two-wheelers doing something completely normal, and your recall figure against C's human labels will be dismal — because the human labeller will not have marked any of it.

This is the single most important adaptation of the method to your context, and it is worth saying out loud in the pitch. It shows you understand the road you are actually measuring.

**Debouncing.** Group by unordered track pair. Within one encounter, emit **one** event carrying the *minimum* TTC observed. Close the encounter when the pair separates or after `debounce_s` without a conflict reading. Without this, "we detected 200 conflicts" means "we detected four conflicts, fifty times each," and any judge who asks how you counted will find it.

### 6.4 Acceptance criteria

- TTC computed on the ground plane, never in pixel space
- All six suppression rules implemented and individually toggleable in config, so their effect can be shown
- **Exactly one event per encounter**, verified by counting events against a hand-checked clip
- On the labelled clip: at least 1 severe and 5 total conflicts
- Every event validates against the schema; failures are logged and dropped, never partially written
- A synthetic test with two vehicles on a known collision course returns an analytically correct TTC — build this fixture first, before any real footage

### 6.5 The tuning trap

C's ground-truth labels arrive around hour 14, and the temptation is to tune thresholds until the numbers look good.

**Do not tune and report on the same data.** That is overfitting, and a judge with a research background will ask precisely the right question about it.

**Split C's labelled footage:**

- **First half — tuning set.** Adjust thresholds and suppression parameters here freely
- **Second half — held-out set.** Touch it once, at hour 18, and report *that* number

A slightly worse honest figure beats a better one you cannot defend. This is a five-minute decision at hour 14 that determines whether your headline claim survives scrutiny.

**Owns:** `edge/conflicts/`

---

## 7. M4 — Motion gate and benchmark · Owner A · P2

### 7.1 Framing — get this right in words before writing code

The motion gate is a **power and thermal contribution, not a throughput rescue.**

It helps when nothing is happening. It does *not* help during a conflict, which is exactly when sustained full-speed inference is needed. The cheap detector must hold 15 FPS on its own whenever there is traffic. Presenting the gate as what makes real-time possible on cheap hardware is an overstatement, and a judge who thinks it through will find the hole.

Its real value: lower average power, less heat in a sealed outdoor box, longer hardware life. Those are good reasons. Say those.

### 7.2 Implementation

- Downscale to greyscale, `cv2.absdiff` against the previous frame, threshold, count changed pixels
- Below the threshold fraction, skip the detector entirely
- Log gate decisions so the invocation reduction can be measured, not asserted

### 7.3 What you can and cannot claim without hardware

Per the earlier discussion, this splits cleanly:

- **The accelerator stage can be estimated.** Hailo's Dataflow Compiler profiler reports expected FPS from a compiled model without any device, and the Model Zoo publishes measured Hailo-8L figures for the YOLOv8 family with downloadable profiler reports. That is a citable, vendor-sourced number
- **The CPU stages cannot be.** Decode, preprocessing, ByteTrack, projection, and pairwise TTC all run on the Pi's CPU. Nothing simulates a Cortex-A76 doing that work, and the pairwise conflict step grows quadratically with vehicle count

Every table this module produces is labelled **"laptop CPU proxy — Raspberry Pi figures pending hardware."** No Pi number is stated as measured. If a Pi 4 can be borrowed from a lab, measure on it and report it as a floor — real hardware one generation old beats perfect emulation of the current one.

### 7.4 Acceptance criteria

- Gate reduces detector invocations by **≥40%** on a clip containing idle periods
- Benchmark table covers three configurations: gate + 320, plain 320, plain 640
- Every figure carries its provenance label
- Harness runs from one command against any video

**Cut line.** Cut freely — it is P2 for a reason. If cut, the deck presents two-tier as architecture and the harness as ready to run.

**Owns:** `edge/gate/`, `bench/`

---

## 8. Event emission · Owner B

- Batch events and `POST /api/events` every 5 seconds or 20 events, whichever first
- On any failure, write to SQLite and retry with exponential backoff
- Idempotent on `event_id` — replaying a buffer must not double-count
- Serialised event **≤ 400 bytes**

**Demonstrate the buffer.** Kill D's server mid-run, let events accumulate, restart it, show them replay. It takes fifteen seconds of demo time and proves an architectural claim that would otherwise be a bullet point.

**Owns:** `edge/emit/`

---

## 9. Schedule

Two people, 24 hours. Bold rows are shared checkpoints.

| Hours | Owner A | Owner B |
|---|---|---|
| **0–1** | **Both: video chosen, repo scaffolded, amendments A1–A3 signed off, `config.yaml` agreed** | |
| 1–2 | YOLOv8n running on the demo video | Frame extracted, reference distances established from satellite or vehicle lengths |
| 2–4 | ByteTrack integrated, `tracks_px.jsonl` writing | Homography solved and validated, `rms_error_m` under 0.5 m |
| 4–6 | Overlay video renderer, class filtering | Projection stage, smoothed velocity, TTC on **synthetic fixture** |
| 6–8 | Track hygiene, switch-rate measurement | TTC on real tracks, first conflicts |
| **8** | **Gate 1 — video in, one real conflict out, in metres** | |
| 8–11 | Motion gate | Suppression rules 1–6, debouncing |
| 11–14 | Benchmark harness | Event emitter, SQLite buffer, POST to D |
| **14** | **Gate 2 — event reaches the server and appears on E's map** | |
| 14–17 | 640 escalation if ahead | **Tune on first half of C's labels only** |
| 17–19 | Provenance labelling on all tables | PET if time; final thresholds |
| **18** | | **Held-out evaluation — run once, report that number** |
| **19** | **Feature freeze. No new functionality.** | |
| 19–22 | Bug fixes, demo clip preparation, buffer-replay rehearsal | |
| 22–24 | Two full rehearsals | |

**B is on the critical path from hour 4.** If A slips, B continues on fixtures. If B slips, nothing downstream has content. Where the two must trade, **A helps B**.

---

## 10. Fixtures A and B own

Committed by hour 2, so neither owner ever blocks on the other:

| File | Purpose |
|---|---|
| `fixtures/tracks_px.sample.jsonl` | 500 lines, two vehicles converging — lets B build everything before A's detector runs |
| `fixtures/tracks_m.sample.jsonl` | Same, projected — lets C and D start |
| `fixtures/synthetic_collision.jsonl` | Two vehicles, known velocities, **analytically computed TTC** — the regression test for Section 6.1 |
| `fixtures/calibration.json` | Plausible homography |
| `fixtures/lane_split.jsonl` | A motorcycle filtering between two cars — the regression test for suppression rule 2 |

The last two matter most. `synthetic_collision` catches maths errors; `lane_split` catches the false-positive class that will otherwise flood your output.

---

## 11. Risks specific to this pair

| Risk | Severity | Response |
|---|---|---|
| Demo footage is ground-level | **Critical** | Nothing here works with heavy occlusion. Resolve before hour 0. No code fixes this |
| Conflict engine floods with lane-splitting events | **High** | Section 6.3 rule 2, and `lane_split.jsonl` as a standing test |
| Calibration silently wrong | High | Held-out validation; bottom-centre projection verified visually at hour 4 |
| Velocity noise creates phantom conflicts | High | Smoothing window; never differentiate raw positions |
| Thresholds tuned on the reporting set | High | Split C's labels, hour 14 |
| Debouncing forgotten, counts meaningless | Medium | Acceptance test counts events against a hand-checked clip |
| A and B edit the same file | Low | Amendment A1 removes the shared file entirely |

---

## 12. Definition of done

A and B are finished when a single command turns the demo video into validated events on D's server, and:

- Distances and speeds are in metres and km/h, traceable to a documented calibration method with a stated error
- One encounter produces exactly one event
- The lane-splitting fixture produces zero conflicts
- The synthetic fixture returns the analytically correct TTC
- Recall and false-positive rate are reported on **held-out** labels, as raw counts
- Every hardware figure carries its provenance label
- The buffer replay demo works with the network unplugged
