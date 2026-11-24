# NETRA — Near-miss Enabled Traffic Risk Analytics

**Smart India Hackathon 2026 · Intelligent Traffic Conflict & Hotspot Analytics**

> Crashes are rare; near-misses are constant. NETRA detects vehicle conflicts in real-world metres and seconds from traffic footage to score junction risk before accidents accumulate.

---

## Architecture & Modules

- **M1 — Homography Calibration & Ground Projection (`edge/calibration/`)**: Maps pixel coordinates to real-world metre ground planes (`rms_error_m < 0.5m`) for accurate spatial measurements.
- **M2 — Detection & Multi-Object Tracking**: Vehicle detection paired with **ByteTrack** multi-object tracking across frames.
- **M3 — Conflict Detection Engine**: Computes **Time-to-Collision (TTC)** and **Post-Encroachment Time (PET)** with suppression rules and event debouncing to filter false positives.
- **M4 — Motion Gate & Benchmark Harness (`bench/`)**: Frame-gating and reproducible CPU/edge throughput benchmarking.
- **M5 — Self-Calibrating Road Norms**: Derives 85th-percentile speed ($V_{85}$), lane clustering, and traffic flow classification directly from observed trajectories.
- **M7 — Edge Emission & Buffering**: Resilient offline-first event buffering and emission from edge to server.
- **M8 — Server & Risk Map (`server/`, `web/`)**: Structured event ingestion API and interactive web dashboard for clicking through junction hotspots.
- **M9 & M10 — Evaluation & Baseline Comparison (`eval/`)**: Ground-truth evaluation (recall & false-positive metrics) benchmarked head-to-head against a pixel-IoU baseline.

---

## Project Structure

```text
├── edge/               # Calibration, projection, tracking, conflict & norms pipeline
├── server/             # Backend API & event ingestion
├── web/                # Interactive risk map frontend
├── bench/              # Throughput & latency benchmark harness
├── eval/               # Ground-truth & pixel-IoU baseline evaluation
├── tests/              # Automated unit & integration tests
├── fixtures/           # Sample calibration & track fixtures
├── ingest_video.py     # End-to-end video ingestion entrypoint
└── verify_all_modules.py
```

---

## Quickstart

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Verify all pipeline modules against fixtures
python verify_all_modules.py

# 3. Run the test suite
pytest

# 4. Ingest a traffic video
python ingest_video.py --help
```
