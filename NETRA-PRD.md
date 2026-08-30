# NETRA — Product Requirements Document

**Near-miss Enabled Traffic Risk Analytics**
Smart India Hackathon 2026 · Intelligent Traffic Violation & Accident Hotspot Analysis

| Field | Value |
|---|---|
| Version | 1.0 |
| Build window | 24-hour Grand Finale sprint |
| Hardware available | **None.** Recorded video files only |
| Team | 6 members (assumed — see Assumptions) |
| Module cut | By pipeline stage, 11 modules |
| Status | Draft for team sign-off before hour 0 |

---

## 0. Assumptions — correct these before the sprint starts

These are inferred, not confirmed. Each one changes the plan if wrong.

1. **Team of six.** Standard SIH size. Ownership below assigns one person per lane. A team of five means M8 folds into the integration owner's lane; a team of four means M10 is cut entirely.
2. **Greenfield codebase.** No working calibration tool, detection loop, or API exists from the internal round. If any of these already run, the affected module drops to "port and harden" and its milestone moves earlier.
3. **One demo video is already chosen** — elevated angle, 10+ minutes, visible conflicts. If not, this is the single highest-priority pre-sprint task. Everything in this document depends on it.
4. **Internet at the venue is unreliable.** Every module has an offline path. No demo step may require a live API call.

---

## 1. Problem and goal

Road hotspots in India are identified only after crashes accumulate, and crash records are sparse and district-level. A junction can stay dangerous for years without generating enough recorded fatalities to trigger intervention.

Crashes are rare; near-misses are constant. NETRA measures the common thing.

**Goal for this sprint:** demonstrate, on recorded footage, that we can detect vehicle conflicts in real metres and seconds, score a location by condition, and produce an engineer-readable output — with a measured accuracy figure rather than an asserted one.

**This is a demo, not a deployment.** Section 3 draws that line explicitly.

---

## 2. The constraint that shapes everything

**We have no Raspberry Pi, no Hailo accelerator, and no camera.**

This is not a minor inconvenience. It invalidates one of the five defensible claims in the pitch deck:

> *"Runs on a ₹15,000 Raspberry Pi — with measured frame rate, power, and thermal figures."*

We cannot measure frame rate, power, or thermals on hardware we do not have. Three options, in order of preference:

| Option | What we say | Risk |
|---|---|---|
| **A — Recommended.** Reframe as a laptop-CPU proxy benchmark, state the Pi figure as a projection with the harness ready to run | "YOLOv8n at 320 INT8 sustains N FPS on a single CPU core; the Hailo-8L is rated at 13 TOPS, so 30 FPS is the design target. Here is the benchmark harness — it runs the moment the board arrives." | Low. Honest, and the harness is itself evidence of rigour |
| B | Drop the hardware claim from the demo narrative entirely | Medium. Loses the edge-native differentiator |
| C | Quote Pi FPS figures from published benchmarks as if measured | **Unacceptable.** A judge who owns a Pi will catch it, and the whole deck loses credibility |

**Decision required at hour 0.** This PRD assumes Option A throughout.

Consequence: **M4 (two-tier detection) drops from a headline feature to P2.** Its value in a 24-hour sprint is the benchmark harness and the honest framing, not a throughput result.

---

## 3. Scope

### In scope (P0 — the demo fails without these)

- Pixel-to-metre calibration for the demo camera angle
- Vehicle detection and multi-object tracking on recorded video
- TTC and PET conflict detection with severity thresholds
- Structured event records, ingested and stored server-side
- A risk map that a judge can click through
- **A recall and false-positive number measured against human labels**

### In scope (P1 — strong, but cuttable at hour 19)

- Self-calibrating road norms (85th-percentile speed at minimum)
- Weather and time enrichment with condition multipliers
- Plain-English incident narration
- Head-to-head comparison against a pixel-IoU baseline

### In scope (P2 — only if genuinely ahead)

- Motion gate and two-tier escalation with laptop benchmark table
- Signal-cycle inference
- Live edge health panel

### Explicitly not in scope

- Any hardware deployment, thermal test, or power measurement
- Training or fine-tuning any model — YOLO and ByteTrack are used as-is
- Fault or blame determination
- Number plate recognition or any biometric identification
- Free-text natural-language database querying
- Multi-camera or multi-site operation
- Authentication, user accounts, or role management
- Rider phone telemetry (dropped in the previous revision — do not reintroduce)

---

## 4. Success criteria

The sprint succeeds if, at demo time, all six of these are true:

| # | Criterion | How it is checked |
|---|---|---|
| S1 | The system ingests a recorded clip end to end without manual intervention | One command, video in, events out |
| S2 | At least one severe conflict (TTC < 0.8 s) is detected and shown | Visible on the map and in the event list |
| S3 | Conflict distances and speeds are reported in metres and km/h, not pixels | Event JSON inspected live |
| S4 | Recall and false-positive rate are stated **with raw counts** | Ground-truth comparison table |
| S5 | The same junction shows different risk under different condition filters | Dashboard filter toggle |
| S6 | Every demo step works with the network cable unplugged | Rehearsed at hour 22 |

**S4 is the one that separates us from the field.** Most competing projects will show detections. Very few will show a measured error rate. Protect it.

---

## 5. Frozen interface contracts

**This section is the mechanism that lets six people work in parallel.** These schemas are frozen at hour 2. After that, changing one requires the integration owner's sign-off and a broadcast to the team.

Every module codes against these contracts, not against another person's implementation. Fixtures (Section 6) mean nobody waits.

### 5.1 `calibration.json`

```json
{
  "video_id": "junction_a_evening",
  "homography": [[0.0,0.0,0.0],[0.0,0.0,0.0],[0.0,0.0,0.0]],
  "reference_points": [
    {"pixel": [412, 688], "ground_m": [0.0, 0.0], "note": "kerb corner"}
  ],
  "rms_error_m": 0.34,
  "location": [13.0106, 74.7943]
}
```

### 5.2 `tracks.jsonl` — one JSON object per line, per track per frame

```json
{"frame": 1240, "t": 41.33, "track_id": 87, "cls": "motorcycle",
 "bbox": [310, 442, 356, 501], "conf": 0.81,
 "ground_m": [14.2, 31.7], "v_mps": [8.1, -2.4]}
```

Producers: M2 writes `bbox`, `conf`, `cls`. M1's homography is applied to produce `ground_m` and `v_mps`.
Rule: a consumer may assume `ground_m` is present only if `calibration.json` was supplied.

### 5.3 `ConflictEvent` — the ~300-byte record

```json
{
  "event_id": "evt_00417",
  "time": "2026-08-28T19:47:12",
  "location": [13.0106, 74.7943],
  "type": "crossing conflict",
  "ttc_s": 0.8,
  "pet_s": 1.4,
  "severity": "severe",
  "vehicle_a": {"type": "motorcycle", "speed_kmh": 47, "direction": "normal"},
  "vehicle_b": {"type": "car", "speed_kmh": 31, "direction": "against flow"},
  "conditions": {"light": "dark", "weather": "light rain", "surface": "wet"},
  "detection_quality": 0.71
}
```

**Hard rules on this schema:**

- `conditions` is written **only** by the server (M6). The edge leaves it null.
- There is **no blame or fault field**, and none may be added. We describe what happened; we do not assign responsibility, because we cannot verify it and being wrong harms a real person.
- `severity` is derived, never hand-set: `ttc_s < 0.8` → `severe`, `ttc_s < 1.5` → `conflict`.

### 5.4 `norms.json`

```json
{
  "speed_85_kmh": 52.0,
  "sample_size": 843,
  "lanes": [{"id": 0, "centreline_m": [[0,0],[40,2]], "heading_deg": 88}],
  "signal_cycle_s": null
}
```

`null` is a valid value for any field. Consumers must degrade gracefully — a missing signal cycle disables one dashboard panel, it does not crash the page.

### 5.5 HTTP API

| Method | Path | Purpose | Owner |
|---|---|---|---|
| `POST` | `/api/events` | Ingest one or many ConflictEvents | D |
| `GET` | `/api/events?from=&to=&light=&weather=` | Filtered event list | D |
| `GET` | `/api/events/{id}/narrative` | Plain-English write-up | F |
| `GET` | `/api/segments` | Scored road segments for the map | D |
| `GET` | `/api/health` | Pipeline status for the live panel | D |

Response envelope for every endpoint:

```json
{"ok": true, "data": {}, "error": null}
```

Frontend codes against this envelope from hour 2, using the fixture server.

---

## 6. Fixture-first rule

**No module may block on another module's output.**

By **hour 2**, the integration owner commits hand-written sample files to `fixtures/`:

- `fixtures/calibration.json` — plausible homography for the demo clip
- `fixtures/tracks.sample.jsonl` — 500 lines covering two vehicles converging
- `fixtures/events.sample.json` — 8 events, mixed severity and conditions
- `fixtures/norms.json` — realistic values
- `fixtures/api/*.json` — canned responses for every endpoint

The frontend owner builds the entire dashboard against fixtures and swaps to the live API at hour 14. The narration owner writes and tests the LLM prompt against `events.sample.json` before a single real event exists.

Fixtures are **append-only**. Nobody edits an existing fixture; a new case gets a new file.

---

## 7. Module specifications

Priority key: **P0** demo fails without it · **P1** strong, cuttable at hour 19 · **P2** only if ahead.

---

### M1 — Calibration tool

| | |
|---|---|
| Owner | **B** |
| Priority | **P0** |
| Depends on | Demo video only |
| Blocks | M3, and the `ground_m` field everywhere |

**Purpose.** A camera sees a flat image and cannot distinguish 2 metres from 20. This module establishes the conversion once, for one camera position.

**Inputs.** One extracted frame from the demo video; four or more points whose real-world separations are known or estimable from lane widths and vehicle lengths.

**Outputs.** `calibration.json` per Section 5.1.

**Acceptance criteria.**
- Re-projecting a held-out reference distance lands within **10%** of its known value.
- The tool runs start to finish in under 2 minutes for a new camera angle.
- `rms_error_m` is reported and stored, not silently discarded.

**Cut line.** If the interactive point-picker is not working by hour 6, hardcode the homography matrix for the single demo clip and move on. The demo does not require the tool to be interactive; it requires the numbers to be right.

**Owns.** `edge/calibration/`

---

### M2 — Vehicle detection and tracking

| | |
|---|---|
| Owner | **A** |
| Priority | **P0** |
| Depends on | Demo video |
| Blocks | M3, M5, M9, M10 |

**Purpose.** Produce continuous vehicle paths. A single frame tells you nothing about danger; paths are what matter.

**Inputs.** Video file path, YOLOv8n weights, confidence threshold.

**Outputs.** `tracks.jsonl` per Section 5.2.

**Acceptance criteria.**
- Fewer than **5 identity switches per 1,000 frames** on the demo clip, verified by spot-checking 20 tracks against an overlay video.
- Sustains **≥15 FPS** at 320×320 on one laptop CPU core, measured and recorded.
- Classes limited to: `car`, `motorcycle`, `truck`, `bus`, `auto`, `pedestrian`. Anything else is dropped.
- Tracks shorter than 5 frames are discarded before writing.

**Cut line.** If ByteTrack tuning eats time, accept a higher ID-switch rate and note it in the accuracy table. Do not switch trackers mid-sprint.

**Owns.** `edge/detect/`, `edge/track/`

---

### M3 — Near-miss detection engine

| | |
|---|---|
| Owner | **B** |
| Priority | **P0** |
| Depends on | M1 (calibration), M2 (tracks) — unblocked by fixtures |
| Blocks | M7, M9, M10 |

**Purpose.** For every pair of vehicles: if both continue as they are, how many seconds until they occupy the same space?

**Inputs.** `tracks.jsonl`, `calibration.json`.

**Outputs.** `events.jsonl` — ConflictEvent records with `conditions` left null.

**Acceptance criteria.**
- TTC computed pairwise on the ground plane, not in pixel space.
- PET computed as the gap between one vehicle clearing a crossing point and the next arriving.
- Thresholds applied exactly: `< 1.5 s` conflict, `< 0.8 s` severe.
- On the labelled clip, produces at least **1 severe and 5 total conflicts**.
- No event references a track shorter than 5 frames or a speed above 150 km/h — both indicate tracking failure, not danger.
- Every emitted event validates against the schema. A failing event is logged and dropped, never partially written.

**Cut line.** PET is the first thing to go. TTC alone is a complete story.

**Owns.** `edge/conflicts/`

---

### M4 — Two-tier detection and benchmark

| | |
|---|---|
| Owner | **A** |
| Priority | **P2** — demoted because we have no Pi |
| Depends on | M2 |
| Blocks | Nothing |

**Purpose.** Demonstrate the edge-efficiency design and produce a benchmark harness that runs the moment hardware arrives.

**Framing — read this before writing any slide copy.** The motion gate is a **power and thermal contribution, not a throughput rescue**. It saves energy when nothing is happening. It does not help during a conflict, when sustained full-speed inference is exactly what is needed. The cheap detector must hold 15 FPS on its own whenever there is traffic.

**Inputs.** Demo video, three configurations: gate + 320, plain 320, plain 640.

**Outputs.** A benchmark table — FPS, CPU utilisation, and detector invocations per minute for each configuration.

**Acceptance criteria.**
- The motion gate reduces detector invocations by **≥40%** on a clip containing idle periods.
- The output table is labelled **"laptop CPU proxy — Raspberry Pi figures pending hardware."** No Pi number is stated as measured.
- The harness runs from one command against any video and any device.

**Cut line.** Cut freely. If cut, the deck states the two-tier design as architecture, and the benchmark harness as ready-to-run.

**Owns.** `edge/gate/`, `bench/`

---

### M5 — Self-calibrating road norms

| | |
|---|---|
| Owner | **C** |
| Priority | **P1** |
| Depends on | M2 |
| Blocks | Nothing hard — enriches M3 output |

**Purpose.** To say a vehicle is speeding you need a speed limit; to say wrong-lane you need a lane map. In India that data mostly does not exist in machine-readable form. So the road teaches us its own rules.

| Norm | Method |
|---|---|
| Speed limit | The speed 85 of 100 vehicles stay under — genuinely how engineers set limits |
| Lane positions | Cluster the trajectories; where paths bunch, that is a lane |
| Lane direction | Whichever way nearly everyone goes is correct; anyone else is against the flow |
| Signal cycle | Vehicles cross the stop line in bunches; the bunches reveal the cycle |

**Outputs.** `norms.json` per Section 5.4.

**Acceptance criteria.**
- 85th-percentile speed computed from **≥200 completed tracks**; sample size stored alongside the value.
- Lane clusters rendered as an overlay image that visibly matches the road.
- A manually reversed test track is flagged `against flow`.

**Cut line, in this order.** Signal cycle first, then lane clustering, then lane direction. **Keep the 85th-percentile speed** — it is the cheapest to compute and the easiest to defend, because it is standard traffic-engineering practice.

**Owns.** `edge/norms/`

---

### M6 — Weather and time layer

| | |
|---|---|
| Owner | **D** |
| Priority | **P1** |
| Depends on | M7 (ingest) |
| Blocks | M11 condition filters |

**Purpose.** Danger is not a property of a place. It is a property of a place under conditions. A junction can be fine in dry daylight and lethal in wet darkness.

**Runs on the server, never the edge.** The edge sends a timestamp and a location; the server attaches conditions. This keeps the edge simple and removes any roadside API dependency.

**Attached to every event.** Hour of day · weekday or weekend · daylight, dusk or dark · peak or off-peak · rain and intensity · fog · wet or dry surface · temperature.

**Two problems this module must handle — these are as valuable to present as the feature itself.**

1. **Detection degrades in rain and darkness.** Raw conflict counts therefore drop in bad weather for the wrong reason. Ignore this and you conclude rain is safe, which is nonsense. **Fix:** carry `detection_quality` (detections per minute, mean confidence) alongside every event and normalise conflict rates by it.

2. **Splitting data across condition buckets leaves each bucket thin.** **Fix:** do not build a model per condition. Build one base risk per location and apply multipliers:
   ```
   risk = base_risk(location) × rain_factor × night_factor × peak_factor
   ```
   Fewer parameters to estimate, far more stable on limited data.

**Acceptance criteria.**
- Light state derived from `astral`, which is **offline** — no network required.
- Weather from a cached lookup table committed to the repo. A live API is a bonus path, never the demo path.
- If weather lookup fails, the event still stores light and time, and `weather` is null.

**Cut line.** Ship light and time only. `astral` works offline and gives us the day/dark split, which carries most of the story.

**Owns.** `server/enrich/`

---

### M7 — Event output, buffer and ingest

| | |
|---|---|
| Owner | **D** |
| Priority | **P0** |
| Depends on | Contracts only |
| Blocks | M6, M11 |

**Purpose.** Move a few hundred bytes per conflict from edge to server. Video stays where it was captured.

**Acceptance criteria.**
- A serialised ConflictEvent is **≤ 400 bytes**.
- Events buffer to SQLite when `POST /api/events` fails, and replay automatically on reconnect. Demonstrated by killing the server mid-run and restarting it.
- Ingest is idempotent on `event_id` — replaying a buffer twice does not double-count.
- Stored in PostgreSQL with PostGIS. **SQLite is an acceptable substitute if Postgres setup exceeds 45 minutes** — the demo does not depend on spatial indexing at this data volume.

**Owns.** `server/api/`, `edge/emit/`

---

### M8 — Plain-English incident writer

| | |
|---|---|
| Owner | **F** |
| Priority | **P1** |
| Depends on | Contracts only — builds against fixtures from hour 2 |
| Blocks | Nothing |

**Purpose.** Turn a structured record into prose an engineer reads without decoding JSON.

**Example output:**

> At 7:47 pm on Thursday, after dark and in light rain, a motorcycle travelling at 47 km/h approached from the north as a car entered from the east against the usual flow of traffic. The two came within 0.8 seconds of collision. This junction has recorded 23 similar conflicts this month, 16 of them in wet conditions after sunset.

**Three rules that make this safe rather than dangerous:**

1. **Server-side only.** A Raspberry Pi cannot run a useful language model, and this must never be on the critical path of conflict detection.
2. **It describes only what is in the record.** The model receives the JSON and is instructed to describe these fields, invent nothing, speculate about nothing, and assign blame to nobody. It is a translator from numbers to sentences, not an analyst.
3. **The structured record stays the source of truth.** The prose is a view of the data, never a replacement. If the two ever disagree, the numbers win.

**Acceptance criteria.**
- Output ≤ 80 words.
- **Field-presence assertion:** for 10 sample events, every number appearing in the prose is traceable to a field in the source record. Any hallucinated figure is a build failure.
- Null fields are omitted from the prose, not guessed at.

**Cut line — and do this deliberately, not as a fallback.** **Pre-generate narratives for the demo events at hour 20 and cache them to disk.** This removes live API latency and network dependency from the demo entirely. Live generation is a nice-to-have; cached output is indistinguishable to the judge and cannot fail on stage.

**Explicitly not built.** Free-text questions turned into database queries. It looks impressive and it silently produces wrong answers. In a system about road safety, that trade is not worth making.

**Owns.** `server/narrate/`

---

### M9 — Human ground-truth check

| | |
|---|---|
| Owner | **C** |
| Priority | **P0 — this is the evidence** |
| Depends on | M3 output; labelling starts immediately, in parallel |
| Blocks | M10 |

**Purpose.** "We detected 200 conflicts" is not evidence unless something confirms those were real. With the rider tier dropped, this is our primary validation.

**Method.** A person watches 30 minutes of the demo footage and marks every genuine near-miss by hand, before seeing system output. Compare against what the system found.

**Label format** — `eval/groundtruth/labels.csv`:

```
t_start_s,t_end_s,severity,vehicle_a,vehicle_b,notes
```

**Acceptance criteria.**
- **≥30 minutes** labelled.
- Labelling is done **blind** — the labeller does not see system output first. This is the difference between validation and confirmation bias.
- Results reported as **raw counts, not just percentages**: caught N of M, invented K false positives, missed J.
- A borderline-case policy is written down before labelling starts, so the labeller is consistent.

**Cut line.** Reduce to 15 minutes. Do not cut below that — the counts stop being meaningful.

**Start this at hour 2.** It is the only P0 task that a non-coding team member can own, it runs fully parallel to development, and it takes wall-clock time that cannot be compressed later.

**Owns.** `eval/groundtruth/`

---

### M10 — Head-to-head comparison harness

| | |
|---|---|
| Owner | **C** |
| Priority | **P1** |
| Depends on | M2 tracks, M9 labels |
| Blocks | Nothing |

**Purpose.** Turn "ours is better" into something a judge can see.

**The argument.** Existing open-source projects judge closeness by how much two bounding boxes overlap on screen. That is broken in two directions: a car 30 metres behind another overlaps heavily on screen with zero risk, and two genuinely close vehicles at the far end of the frame barely overlap at all. It also only fires *after* the boxes overlap — ours fires 1–2 seconds before.

**Implementation.** The IoU-proximity method in perhaps thirty lines, running beside ours on the same clip, both scored against the same human labels.

**Acceptance criteria.**
- Both methods scored against `labels.csv` with identical criteria.
- Output is a single table: method, recall, false positives, mean lead time before the encounter.
- **At least one clip** where the IoU baseline misses a conflict our method catches, cut and ready to play.

**Cut line.** Cut the table, keep the single clip. One clip showing the failure mode is worth more demo time than a table of numbers.

**Owns.** `eval/baseline/`

---

### M11 — Risk map dashboard

| | |
|---|---|
| Owner | **E** |
| Priority | **P0** |
| Depends on | API contract only — builds against fixtures from hour 2 |
| Blocks | Nothing |

**Purpose.** The surface the judge actually looks at.

**Required (P0).**
- Leaflet map, road segments coloured by risk
- Click a segment → conflict count, time-of-day chart, speed distribution with the learned 85th-percentile limit marked
- Event list with severity and timestamp
- Video clip playback for a detected conflict

**Required (P1).**
- **Condition filters** — the same map under "rain, after dark" versus "dry, daytime". This is the visual proof of the condition-aware claim; without it M6 is invisible.
- Plain-English write-up displayed alongside each event

**Optional (P2).**
- Live panel showing pipeline frame rate and escalation frequency

**Acceptance criteria.**
- Loads and renders from fixtures with the backend stopped. **No blank screen on API failure** — degrade to cached data with a visible "offline" badge.
- Every panel handles null: a missing signal cycle hides one card, it does not white-screen the app.
- Tested at the actual projector resolution before hour 22.

**Owns.** `web/`

---

## 8. Ownership map — the anti-conflict rule

**You may only edit files inside your own directories.** A change anywhere else goes through that directory's owner. `contracts/` requires integration-owner sign-off.

```
netra/
├── contracts/            # F (lead) — frozen at hour 2, sign-off required
├── fixtures/             # F — append-only, never edited
├── edge/
│   ├── calibration/      # B
│   ├── detect/           # A
│   ├── track/            # A
│   ├── gate/             # A
│   ├── conflicts/        # B
│   ├── norms/            # C
│   └── emit/             # B
├── server/
│   ├── api/              # D
│   ├── enrich/           # D
│   ├── scoring/          # D
│   └── narrate/          # F
├── web/                  # E
├── eval/
│   ├── groundtruth/      # C
│   └── baseline/         # C
├── bench/                # A
└── demo/                 # F
```

| Owner | Lane | Modules |
|---|---|---|
| **A** | Vision | M2, M4 |
| **B** | Geometry & conflicts | M1, M3, event emission |
| **C** | Norms & evidence | M5, M9, M10 |
| **D** | Server | M6, M7 |
| **E** | Frontend | M11 |
| **F** | Narration, integration, demo | M8, contracts, fixtures, demo script |

**Branching.** One branch per owner, named for the lane. Merge to `main` only at the three integration gates (hours 8, 14, 19). No merges between gates — that is what causes the conflicts this structure exists to prevent.

---

## 9. 24-hour timeline

| Hours | Phase | Gate |
|---|---|---|
| 0–2 | Kickoff. Demo video chosen. Contracts written and frozen. Fixtures committed. Repo scaffolded. **Hardware-claim decision made.** | Every owner can run `hello world` in their own directory |
| 2–8 | Parallel build. **M9 labelling starts now** and runs in the background. | **Gate 1:** vertical slice — video in, one conflict event out, in metres |
| 8–14 | Server ingest, enrichment, scoring. Frontend swaps from fixtures to live API. | **Gate 2:** an event travels edge → server → map, end to end |
| 14–19 | Norms, comparison harness, narration. Accuracy table computed. | **Gate 3:** all P0 criteria met |
| **19** | **Feature freeze.** No new functionality after this point, no exceptions. | |
| 19–22 | Integration, bug fixing, demo data preparation, narrative pre-generation | Full run-through with network unplugged |
| 22–24 | Two full rehearsals. Slide and script alignment. **Sleep if possible.** | Everyone can run the demo, not just its author |

**The feature freeze at hour 19 is the single most important line in this document.** Teams lose finales by integrating at hour 23.

---

## 10. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Demo video is ground-level; vehicles occlude constantly** | **Critical — decides the project** | Choose an elevated clip (footbridge, upper floor, rooftop) **before hour 0**. Have a second clip as backup. At ground level the geometry never works |
| No hardware to substantiate edge claims | High | Option A framing (Section 2). Benchmark harness as the artefact |
| Calibration is wrong; every distance is wrong silently | High | `rms_error_m` reported and sanity-checked against a known lane width at Gate 1. Wrong calibration is worse than none, because it looks fine |
| Ground-truth labelling starts too late | High | Starts at hour 2, owned by C, blocking nothing else |
| Integration deferred to the end | High | Three mandatory gates; branch merges only at gates |
| Venue network fails during the demo | Medium | Every path has an offline mode. Narratives pre-generated. Rehearsed unplugged at hour 22 |
| Detection degrades in the clip's rain or dark sections | Medium | `detection_quality` tracked and normalised for — and mentioned aloud, because spotting it is worth as much as fixing it |
| LLM writes something untrue | Medium | Record-only prompt, field-presence assertion, cached output |
| Scope creep past hour 19 | Medium | Feature freeze, enforced by the integration owner |

---

## 11. Demo script (5 minutes)

1. **The gap, in 30 seconds.** Hotspots are found only after crashes accumulate. Crash data is sparse and district-level. A junction stays dangerous, invisibly, for years.
2. **The shift.** Near-misses are roughly 100× more frequent than crashes. Measure the common thing; the map fills in days rather than years.
3. **Live run.** Play the clip. Show a conflict detected, with TTC in seconds and speeds in km/h. Emphasise: metres, not pixel overlap.
4. **The comparison clip.** The pixel-IoU baseline misses it. Ours catches it 1.4 seconds earlier.
5. **The accuracy table.** Caught N of M, K false positives, against blind human labels. *This is the moment that separates us.*
6. **Condition filters.** Same junction, dry daylight versus wet after dark. "This junction does not need redesign — it needs lighting and drainage."
7. **The write-up.** One plain-English paragraph an engineer can act on.
8. **Honest close.** What is not yet proven: hardware figures. Here is the harness that measures them the day the board arrives.

Point 8 is not a weakness. Volunteering the limit you have not yet cleared is the strongest credibility signal available to you, and judges reward it.

---

## 12. Open questions

| # | Question | Owner | Needed by |
|---|---|---|---|
| Q1 | Which demo video, and is the camera angle genuinely elevated? | All | **Before hour 0** |
| Q2 | Option A, B or C on the hardware claim? | Lead | Hour 0 |
| Q3 | Does any code exist from the internal round? | All | Hour 0 |
| Q4 | Per-site cost figure — is Rs 15,000 the board alone or the full BOM including camera, enclosure and mount? | Lead | Before slides are final |
| Q5 | Who labels the ground truth, and are they available from hour 2? | C | Hour 1 |
