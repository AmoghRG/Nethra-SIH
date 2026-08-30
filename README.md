# NETRA

Camera-based near-miss detection for Indian urban roads. NETRA turns an
ordinary traffic camera feed into calibrated ground-plane tracks, scores
vehicle-pair conflicts (TTC / PET), and emits compact safety events that a
server enriches, scores and narrates for a web dashboard.

Built for SIH.

## Why

Crashes are counted after they happen. Near-misses happen far more often and
are never recorded, so hazardous locations are only identified once someone is
hurt. NETRA measures the near-misses.

## Pipeline

    video -> motion gate -> detect (YOLO) -> track -> calibrate to metres
          -> conflict engine (TTC/PET + suppression + debounce)
          -> events -> buffer/upload -> server enrich/score/narrate -> web

## Layout

| path | what |
|---|---|
| `edge/` | the on-camera pipeline: calibration, detect, track, gate, conflicts, emit |
| `edge/config.yaml` | every tuned constant; override at the CLI with `--set key=value` |
| `contracts/` | JSON schemas and the API contract between edge, server and web |
| `fixtures/` | synthetic and recorded test data (append-only) |
| `tests/edge/` | the edge test suite |
| `bench/` | throughput benchmark and results |
| `server/` | ingest, enrichment, scoring, narration |
| `web/` | dashboard |
| `demo/` | demo artefacts generated from real pipeline output |
| `eval/` | ground-truth labelling and evaluation |

## Run it

```bash
# one time: the two deps that were missing
py -3.14 -m pip install -r requirements.txt
py -3.14 -m pip install -r server\api\requirements.txt

# every time
cd server\api
py -3.14 -m uvicorn netra_server:app --port 800
```

The detector extras (`ultralytics`, `opencv-python`, `lap`) are lazy-imported,
so everything above except the video path runs without them.

## Measured on the demo clip

| criterion | result | |
|---|---|---|
| >= 15 FPS at 320x320 on one CPU core | 37.4 FPS, 31.8 FPS/core | PASS |
| < 5 identity switches per 1,000 frames | 0.67 | PASS |
| calibration `rms_error_m` under 0.5 m | 0.136 m (held out) | PASS |
| serialised event <= 400 bytes | 353 bytes worst case | PASS |
| motion gate cuts detector calls >= 40% | 0.0% | see note |

The gate skips frames only when nothing moves; the demo road never goes quiet,
so on this clip the gate is pure overhead. It is a power and thermal feature
and this footage cannot demonstrate it.

Conflict output on the same clip: 8 events (4 severe) from 27 raw readings,
against 1,379 with every suppression rule disabled.

## Conventions

- SI internally (metres, seconds, m/s); km/h only at JSON serialisation.
- Ground frame: X east, Y north; `heading_deg` is a compass bearing in `[0,360)`.
- `t` is seconds from video start, never wall-clock.
- Bottom-centre of the bbox is the ground contact point.
- No tuned constant is hardcoded — it lives in `edge/config.yaml` or it is a bug.
- `severity` derives from `ttc_s`. There is no blame or fault field, ever.

Development notes, ownership and the traps already hit are in `CLAUDE.md`.
