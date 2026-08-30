"""
NETRA System Verification Script
Executes comprehensive verification across all currently present modules:
  - M1: Homography Calibration & Ground Projection
  - M2: Detection & ByteTrack Tracker Pipeline
  - M3: Conflict Detection Engine (TTC, PET, Suppression Rules, Debounce)
  - M4: Motion Gate & Benchmark Harness
  - M5: Road Norms Engine (V85 Speed, Lane Clustering, Flow Classification)
  - M7: Edge Emission & Buffer
  - M9: Ground Truth Check & Metric Evaluation
  - M10: Comparative IoU Baseline Harness
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
import json


def print_header(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def verify_module(name: str, test_func) -> bool:
    print(f"\n[TESTING] {name} ...", end=" ", flush=True)
    t0 = time.perf_counter()
    try:
        test_func()
        dt = time.perf_counter() - t0
        print(f" [PASS] ({dt:.3f}s)")
        return True
    except Exception as e:
        dt = time.perf_counter() - t0
        print(f" [FAIL] ({dt:.3f}s)")
        print(f"  --> Error: {e}", file=sys.stderr)
        return False


def test_m1_calibration():
    from edge.calibration.homography import load_calibration, project_point
    calib = load_calibration("fixtures/calibration.json")
    assert calib.rms_error_m < 0.5, "RMS error must be under 0.5m"
    pt = project_point(calib.H, (640, 600))
    assert pt is not None and len(pt) == 2


def test_m1_projection():
    from edge.calibration.homography import load_calibration
    from edge.calibration.project import project_tracks
    from edge.common.config import load_config
    from edge.common.jsonl import read_jsonl

    calib = load_calibration("fixtures/calibration.json")
    cfg = load_config("edge/config.yaml")
    rows = list(read_jsonl("fixtures/tracks_px.sample.jsonl"))
    out, stats = project_tracks(rows, calib, cfg)
    assert len(out) > 0, "Must project tracks successfully"
    assert stats.rows_out > 0


def test_m2_detector_and_tracker():
    from edge.detect.detector import Detector
    from edge.track.tracker import Tracker
    from edge.common.config import load_config
    cfg = load_config("edge/config.yaml")
    det = Detector(cfg)
    tr = Tracker(cfg)
    assert det is not None
    assert tr is not None


def test_m3_conflict_engine():
    from edge.calibration.homography import load_calibration
    from edge.common.config import load_config
    from edge.common.jsonl import load_jsonl
    from edge.conflicts.engine import ConflictEngine

    calib = load_calibration("fixtures/calibration.json")
    cfg = load_config("edge/config.yaml")
    rows = load_jsonl("fixtures/tracks_m.sample.jsonl")
    engine = ConflictEngine(cfg, calib)
    events = engine.run(rows)
    assert len(events) >= 1, "Must detect conflicts in sample data"
    assert all(e.byte_size() <= 400 for e in events), "All events must be <= 400 bytes"


def test_m4_motion_gate():
    import numpy as np
    from edge.common.config import load_config
    from edge.gate.motion_gate import MotionGate
    cfg = load_config("edge/config.yaml")
    gate = MotionGate(cfg)
    f1 = np.zeros((320, 320, 3), dtype=np.uint8)
    f2 = np.ones((320, 320, 3), dtype=np.uint8) * 100
    res1 = gate.should_detect(f1)
    res2 = gate.should_detect(f2)
    assert isinstance(res1, bool)
    assert isinstance(res2, bool)


def test_m5_road_norms():
    from edge.norms.norms_engine import load_tracks_jsonl, run_norms_pipeline
    tracks = load_tracks_jsonl(Path("fixtures/tracks.sample.jsonl"))
    norms = run_norms_pipeline(tracks)
    assert "speed_85_kmh" in norms
    assert "lanes" in norms
    assert isinstance(norms["speed_85_kmh"], (int, float))


def test_m7_buffer_and_uploader():
    from edge.common.config import load_config
    from edge.common.event import ConflictEvent, Vehicle
    from edge.emit.buffer import EventBuffer
    cfg = load_config("edge/config.yaml")
    buf = EventBuffer(":memory:")
    evt = ConflictEvent.build(
        video_id="junction_a_evening",
        video_start="2026-08-30T11:00:00",
        location=[13.0106, 74.7943],
        conflict_type="crossing conflict",
        ttc_s=0.75,
        pet_s=1.2,
        vehicle_a=Vehicle("car", 45.0),
        vehicle_b=Vehicle("motorcycle", 38.0),
        detection_quality=0.85,
        track_ids=[101, 102],
        t_video_s=12.4,
        min_ttc_frame=372,
    )
    buf.add([evt.to_dict()])
    pending = buf.pending()
    assert len(pending) == 1
    assert pending[0]["event_id"] == evt.event_id
    buf.mark_sent([evt.event_id])
    assert len(buf.pending()) == 0


def test_m9_ground_truth_evaluator():
    from eval.groundtruth.evaluator import evaluate_ground_truth
    gt = [
        {"id": "gt_1", "t_start_s": 10.0, "t_end_s": 12.0, "severity": "severe", "vehicle_a": "car", "vehicle_b": "bus"},
    ]
    detected = [
        {"event_id": "evt_1", "t_sec": 10.5, "severity": "severe"},
    ]
    report = evaluate_ground_truth(gt, detected)
    assert report["summary"]["recall"] == 1.0
    assert report["summary"]["precision"] == 1.0


def test_m10_baseline_iou():
    from eval.baseline.baseline_iou import compute_2d_iou
    b1 = [0, 0, 10, 10]
    b2 = [5, 0, 15, 10]
    iou = compute_2d_iou(b1, b2)
    assert 0.3 < iou < 0.4


def test_edge_pipeline_dry_run():
    from edge.run_pipeline import main as run_pipeline_main
    res = run_pipeline_main(["--dry-run"])
    assert res == 0, "Full pipeline dry-run must succeed"


def main():
    print_header("NETRA SYSTEM VERIFICATION TEST")
    print("Verifying all currently present modules and their interconnections...\n")

    modules = [
        ("M1 Calibration (Homography & Math)", test_m1_calibration),
        ("M1 Projection (Pixel -> Ground Metres)", test_m1_projection),
        ("M2 Vehicle Detector & ByteTrack Tracker", test_m2_detector_and_tracker),
        ("M3 Conflict & Near-Miss Engine (TTC/PET)", test_m3_conflict_engine),
        ("M4 Motion Gate & Thermal Filter", test_m4_motion_gate),
        ("M5 Self-Calibrating Road Norms Engine", test_m5_road_norms),
        ("M7 Edge Event Buffer & Offline Queue", test_m7_buffer_and_uploader),
        ("M9 Ground Truth Validation Engine", test_m9_ground_truth_evaluator),
        ("M10 Comparative Pixel-IoU Baseline", test_m10_baseline_iou),
        ("Full End-to-End Pipeline Chain (Dry-Run)", test_edge_pipeline_dry_run),
    ]

    results = []
    for name, func in modules:
        passed = verify_module(name, func)
        results.append((name, passed))

    print_header("VERIFICATION SUMMARY")
    all_passed = True
    for name, passed in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {status:<8} : {name}")
        if not passed:
            all_passed = False

    print("=" * 70)
    if all_passed:
        print("  ALL PRESENT MODULES ARE CONNECTED AND WORKING CORRECTLY! [10/10 PASS]")
    else:
        print("  SOME MODULES FAILED VERIFICATION. CHECK LOGS ABOVE.")
    print("=" * 70)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
