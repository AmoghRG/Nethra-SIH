"""M2 CLI - video in, tracks_px.jsonl out. Owner A.

    python -m edge.track.run_track --video data/junction.mp4 \\
        --calib fixtures/calibration.json --out out/tracks_px.jsonl \\
        --overlay out/overlay.mp4

Writes pixels only. Amendment A1 means owner B's projection stage turns this
into ``tracks_m.jsonl``; nothing here ever writes a ground-plane field.

**Where the validity polygon is applied, and why it is not where the PRD says.**
Amendment A2 asks for detections outside the polygon to be discarded *before*
tracking. Ultralytics' integrated ``model.track()`` exposes no seam between
detection and association, so the polygon is applied to the tracker's **output**
instead. Downstream the effect is identical - those rows never reach projection
- and it is arguably better for identity continuity, because the tracker still
sees the full frame and does not lose a vehicle that briefly clips the edge.
The cost is that the tracker spends a little work on boxes we then discard.
Say this rather than implying the filter runs earlier than it does.

Needs ultralytics and opencv-python. Everything in owner B's lane runs without
them.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from edge.calibration.homography import load_calibration
from edge.common.config import add_config_args, config_from_args
from edge.common.geometry import point_in_polygon
from edge.common.jsonl import write_jsonl
from edge.common.threads import current as pinned_threads, pin_threads
from edge.gate.motion_gate import MotionGate
from edge.track.hygiene import (
    drop_short_tracks,
    drop_stationary_tracks,
    measure_switch_rate,
)
from edge.track.tracker import Tracker

#: Acceptance criterion from both PRDs: ">=15 FPS at 320x320 on ONE laptop CPU
#: core". The per-core part is the whole point. A number produced by twenty
#: cores says nothing about a Raspberry Pi, and quoting it as if it did is the
#: same mistake as quoting a Pi FPS figure we never measured.
FPS_TARGET = 15.0


def _open_video(path: str) -> tuple[Any, float, tuple[int, int], int]:
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "reading video needs OpenCV: pip install -r requirements.txt"
        ) from exc
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("could not open video: {}".format(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    return cap, float(fps), size, total


def run(args: argparse.Namespace) -> dict[str, Any]:
    cfg = config_from_args(args)
    pin_threads(int(getattr(args, "threads", 0) or 0))
    calib = load_calibration(args.calib) if args.calib else None
    region = calib.valid_region_px if calib else None

    cap, fps, size, total = _open_video(args.video)
    tracker = Tracker(cfg)
    gate = MotionGate(cfg)
    if args.gate:
        gate.enabled = True

    overlay = None
    if args.overlay:
        from edge.detect.overlay import OverlayWriter

        overlay = OverlayWriter(args.overlay, size, fps, region)

    rows: list[dict[str, Any]] = []
    frame_no = 0
    rejected_outside = 0
    # Wall clock around decode + gate + inference + association, which is what
    # "sustains N FPS" has to mean. Writing the JSONL is excluded because it is
    # a debug artefact, not part of the edge loop.
    t0 = time.perf_counter()
    cpu0 = time.process_time()

    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = None

    target_total = args.max_frames if (args.max_frames and args.max_frames < total) else total
    pbar = tqdm(total=target_total, desc="Running Inference & Tracking", unit="frame", dynamic_ncols=True) if tqdm and target_total > 0 else None

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if args.max_frames and frame_no >= args.max_frames:
                break

            gated = not gate.should_detect(frame)
            dets = [] if gated else tracker.update(frame)

            kept = []
            for det in dets:
                x1, _, x2, y2 = det.bbox
                if region and not point_in_polygon(((x1 + x2) / 2.0, y2), region):
                    rejected_outside += 1
                    continue
                kept.append(det)
                rows.append({
                    "frame": frame_no,
                    "t": round(frame_no / fps, 3),
                    "track_id": det.track_id,
                    "cls": det.cls,
                    "bbox": [round(v, 1) for v in det.bbox],
                    "conf": round(det.conf, 3),
                })

            if overlay is not None:
                overlay.write(frame, kept, frame_no, gated)
            frame_no += 1
            if pbar is not None:
                pbar.update(1)
                pbar.set_postfix({"tracked": len(kept)})
    finally:
        if pbar is not None:
            pbar.close()
        cap.release()
        if overlay is not None:
            overlay.close()

    wall = time.perf_counter() - t0
    cpu = time.process_time() - cpu0

    rows, dropped_short = drop_short_tracks(rows, int(cfg.get("tracker.min_track_frames")))
    rows, dropped_static = drop_stationary_tracks(rows)
    switches = measure_switch_rate(rows)

    achieved_fps = frame_no / wall if wall > 0 else 0.0
    stats = {
        "video": args.video,
        "frames": frame_no,
        "wall_s": round(wall, 2),
        "fps": round(achieved_fps, 2),
        "fps_target": FPS_TARGET,
        "meets_fps_target": achieved_fps >= FPS_TARGET,
        "cpu_core_utilisation": round(cpu / wall, 2) if wall > 0 else 0.0,
        "threads": pinned_threads(),
        # FPS divided by the cores that actually produced it. This is the
        # number the acceptance criterion is really about, and it is the one
        # that survives contact with a four-core Raspberry Pi.
        "fps_per_core": round(achieved_fps / max(cpu / wall, 1e-9), 2) if wall > 0 else 0.0,
        "imgsz": int(cfg.get("detector.imgsz")),
        "rows": len(rows),
        "tracks_dropped_short": dropped_short,
        "tracks_dropped_stationary": dropped_static,
        "rejected_outside_region": rejected_outside,
        "gate_enabled": gate.enabled,
        "detector_invocations": gate.stats.detector_invocations,
        "switches_per_1000_frames": round(switches.per_1000_frames, 2),
        "provenance": "laptop CPU proxy - Raspberry Pi figures pending hardware.",
    }

    write_jsonl(args.out, rows, header=(
        "tracks_px.jsonl - AMENDMENT A1 pixel space only, produced by owner A.\n"
        "source {} | {} frames at {:.1f} fps measured | imgsz {}\n"
        "Ground-plane fields are owner B's, added by edge/calibration/project.py."
        .format(Path(args.video).name, frame_no, achieved_fps, stats["imgsz"])
    ))

    print(gate.stats.render() if gate.enabled else "motion gate: disabled")
    print()
    print(switches.render())
    print()
    print("detection and tracking:")
    print("  rows written                {}".format(len(rows)))
    print("  rejected outside validity   {}  (amendment A2, applied post-track)".format(rejected_outside))
    print("  tracks dropped, too short   {}".format(dropped_short))
    print("  tracks dropped, stationary  {}  {}".format(
        len(dropped_static), dropped_static if dropped_static else ""))
    print("  measured                    {:.2f} FPS at {}x{}   (target {:.0f})  {}".format(
        achieved_fps, stats["imgsz"], stats["imgsz"], FPS_TARGET,
        "PASS" if stats["meets_fps_target"] else "BELOW TARGET"))
    print("  threads                     {}".format(stats["threads"]))
    print("  CPU core utilisation        {:.2f}".format(stats["cpu_core_utilisation"]))
    print("  FPS per core                {:.2f}   <- the acceptance number".format(
        stats["fps_per_core"]))
    if stats["cpu_core_utilisation"] > 1.5 and stats["meets_fps_target"]:
        print("  WARNING: that FPS came from {:.1f} cores. The criterion is one".format(
            stats["cpu_core_utilisation"]))
        print("  core. Re-run with --threads 1 before quoting the figure.")
    print("  " + stats["provenance"])
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M2 detection and tracking (owner A)")
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", default="out/tracks_px.jsonl")
    parser.add_argument("--calib", default=None, help="calibration.json, for the A2 polygon")
    parser.add_argument("--overlay", default=None, help="write an annotated video here")
    parser.add_argument("--gate", action="store_true", help="force the motion gate on")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument(
        "--threads", type=int, default=0,
        help="pin torch and OpenCV to N threads; use 1 for the acceptance figure",
    )
    parser.add_argument("--stats-out", default="out/track_stats.json")
    add_config_args(parser)
    args = parser.parse_args(argv)

    stats = run(args)
    Path(args.stats_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.stats_out).write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print("stats written to {}".format(args.stats_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
