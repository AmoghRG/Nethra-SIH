# demo/ — demo script, artefacts and rehearsal assets

**Owner: F.** The artefact generators here were written by A and B; see
`contracts/README.md` for the recorded ownership deviation. They are
presentation scripts — nothing under `edge/` imports them.

---

## The artefacts, and the one command each

All four run off the pipeline's own output: `tracks_px.jsonl`, `events.jsonl`,
and a `--trace` file from the conflict engine.

```bash
# 0. produce the trace the plots need (off by default; it is debug output)
python -m edge.conflicts.run_conflicts --tracks out/demo/tracks_m.jsonl \
    --calib fixtures/calibration.demo_clip.json \
    --out out/demo/events.jsonl --trace out/demo/trace.jsonl

# 1. annotated overlay video, cut to the seconds around the conflict
python -m demo.make_overlay --video data/Road_traffic_video_1.mp4 \
    --tracks out/demo/tracks_px.jsonl --events out/demo/events.jsonl \
    --trace out/demo/trace.jsonl --calib fixtures/calibration.demo_clip.json \
    --around-event --out out/demo/overlay.mp4

# 2. TTC against time, with the thresholds drawn and suppression marked
python -m demo.make_ttc_plot --events out/demo/events.jsonl \
    --trace out/demo/trace.jsonl --calib fixtures/calibration.demo_clip.json \
    --out out/demo/ttc_plot.png

# 3. composite card: frame, plot, and the ConflictEvent JSON
python -m demo.make_panel --video data/Road_traffic_video_1.mp4 \
    --tracks out/demo/tracks_px.jsonl --events out/demo/events.jsonl \
    --plot out/demo/ttc_plot.png --calib fixtures/calibration.demo_clip.json \
    --out out/demo/panel.png

# 4. what each suppression rule removes
python -m demo.make_suppression_compare --tracks out/demo/tracks_m.jsonl \
    --calib fixtures/calibration.demo_clip.json --out out/demo/suppression.png
```

---

## The number to lead with

From `make_suppression_compare` on the five-minute real clip:

| configuration | events | severe |
|---|---|---|
| **all rules on** | **8** | **4** |
| without lane-splitting (rule 2) | 53 | 38 |
| without debounce (rule 6) | 27 | 15 |
| without velocity noise (rule 1) | 12 | 7 |
| without stopped vehicles (rule 3) | 12 | 5 |
| **all rules OFF** | **1379** | **1037** |

**8 against 1379.** That is the PRD's claim — "a naive TTC implementation will
emit hundreds of conflicts per minute and every one will be garbage" — turned
into a measurement. Rule 2 alone accounts for 45 of the difference.

Two rules removed nothing on this clip: `speed_sanity` and `validity_region`.
They are not dead; the clip contains no identity switch severe enough and no
far-field detection. Say that rather than implying the contrast is weak.

---

## Which clip to use for what

| clip | good for | not good for |
|---|---|---|
| `Road_traffic_video_1` (real, 5 min) | **every metric artefact.** Fixed elevated camera, road markings spanning the action, `rms_error_m` 0.136 m | Its scale still rests on an assumed 6 m dash pitch |
| `Road_traffic_video_5` (BeamNG, 6 s) | **detection and tracking on a scripted near-miss.** 99% recall, 0 false positives | Metric artefacts — see below |
| `Road_traffic_video_3` (real, snowy) | nothing; it is a collision with camera cuts | anything |
| `Road_traffic_video_2` | nothing; it is a screen recording of a YouTube player | anything |

### Why the simulation clip carries no metres

Three calibration approaches were tried and all failed, for one reason: **the
scene has no ground-plane reference spanning the area where the vehicles are.**

1. **Crosswalk only.** The five stripes are a genuine, verified ruler — their
   cross-ratio is 1.3437 against the 4/3 expected for uniform spacing, 0.8%
   off. But they occupy a 180 x 130 px patch in the corner while the vehicles
   are a median 768 px away. Extrapolating put the horizon through the conflict
   area and implied speeds ran to 94,000 km/h.
2. **Two vanishing points.** Needs two lines parallel in the world. The
   junction's two roads meet at an angle, and the only long markings are one
   double line and one solid line on different roads.
3. **Crosswalk plus a constant-speed vehicle.** Fitted to rms 3.69 px, but with
   every reference point lying on just two lines there is a family of solutions
   fitting both rulers' cross-ratios, and the solver settled on a degenerate
   one: all four corners of the validity polygon mapped to world x = 0.

A known crosswalk pitch would **not** have fixed any of this — the failure is
conditioning, not the unknown constant. What would fix it: park two vehicles a
measured distance apart in the middle of the junction and re-export, or name
the map so its geometry can be looked up.

So the sim clip gets the overlay (which needs no calibration) and is honest
material for the detection half of the story. The metric artefacts come from
the real clip.

### Detector settings for the sim clip

`--set detector.imgsz=640 --set detector.conf=0.25`, a per-source override
rather than a change to the frozen defaults. Measured on the 149 frames, of
which 119 contain a vehicle:

| config | recall | false positives |
|---|---|---|
| imgsz 320, conf 0.35 (frozen default) | 76% | 20 |
| **imgsz 640, conf 0.25** | **99%** | **0** |
| imgsz 960, conf 0.20 (on the 480p cut) | 98% | 19 |

The last row is the trap: dropping confidence buys recall by inventing vehicles
in an empty junction. On a clip that is 20% empty, recall without a
false-positive count beside it is a misleading number.

---

## The five-minute run

1. **The gap, in 30 seconds.** Hotspots are found only after crashes
   accumulate. Crash data is sparse and district-level. A junction stays
   dangerous, invisibly, for years.
2. **The shift.** Near-misses are roughly 100x more frequent than crashes.
   Measure the common thing; the map fills in days rather than years.
3. **Live run.** Play `overlay.mp4`. Show the TTC readout crossing 1.5 s and
   then 0.8 s. Emphasise: metres, not pixel overlap.
4. **The comparison clip.** The pixel-IoU baseline misses it. Ours catches it
   1.4 seconds earlier.
5. **The accuracy table.** Caught N of M, K false positives, against blind
   human labels. *This is the moment that separates us.*
6. **Condition filters.** Same junction, dry daylight versus wet after dark.
7. **The write-up.** One plain-English paragraph an engineer can act on.
8. **Honest close.** What is not yet proven: hardware figures, and the scale on
   both clips. Here is the harness that measures the first the day the board
   arrives, and here is exactly what would settle the second.

Point 8 is not a weakness. Volunteering the limit you have not yet cleared is
the strongest credibility signal available, and judges reward it.

## Two beats worth rehearsing

**The buffer replay.** Kill D's server mid-run, let events accumulate, restart
it, watch them drain. Fifteen seconds that prove an architectural claim which
would otherwise be a bullet point.

**Suppression, on and off.** `suppression.png`, or live on the lane-split
fixture: 0 events with rule 2 on, 2 severe with it off.

## Non-negotiable

- **Feature freeze at hour 19.** Teams lose finales by integrating at hour 23.
- Full run-through with the network cable unplugged at hour 22.
- Two rehearsals. Everyone can run the demo, not just its author.
