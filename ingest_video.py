"""
NETRA Video Ingestion & End-to-End Conflict Analytics Pipeline
==============================================================

Single-command video ingestion: input any video file, and NETRA will automatically:
  1. Detect & track vehicles with GPU-accelerated YOLOv8 + ByteTrack -> tracks_px.jsonl
  2. Project trajectories to real-world ground metric coordinates (m, km/h) -> tracks_m.jsonl
  3. Compute near-miss conflict risks (TTC, PET, 6 suppression rules, debouncing) -> events.jsonl
  4. Self-calibrate road norms (V85 speed limit, lane clusters, wrong-way detection) -> norms.json
  5. Generate annotated visualization video with live bounding boxes, speeds, and TTC conflict alerts -> annotated_video.mp4
  6. Output a complete executive summary report -> pipeline_summary.json

Usage:
  python ingest_video.py --video path/to/video.mp4
  python ingest_video.py --video path/to/video.mp4 --model yolov8x.pt --imgsz 640 --conf 0.20
  python ingest_video.py   # interactive prompt
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edge.calibration.homography import Calibration, load_calibration, project_point
from edge.calibration.project import project_tracks
from edge.common.config import load_config
from edge.common.geometry import point_in_polygon
from edge.common.jsonl import load_jsonl, read_jsonl, write_jsonl
from edge.common.threads import current as pinned_threads, pin_threads
from edge.conflicts.engine import ConflictEngine
from edge.detect.overlay import CLASS_COLOURS
from edge.gate.motion_gate import MotionGate
from edge.norms.norms_engine import render_lane_overlay, run_norms_pipeline
from edge.track.hygiene import drop_short_tracks, drop_stationary_tracks, measure_switch_rate
from edge.track.tracker import Tracker


def _banner(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def severity_colour(ttc_s: Optional[float]) -> tuple[int, int, int]:
    if ttc_s is None:
        return (200, 200, 200)
    if ttc_s < 0.8:
        return (40, 40, 240)    # Red: severe
    if ttc_s < 1.5:
        return (40, 180, 240)   # Amber: conflict
    return (80, 220, 80)        # Green: safe


def generate_annotated_video(
    video_path: Path,
    tracks_px_path: Path,
    events_path: Path,
    out_video_path: Path,
    calib: Optional[Calibration] = None,
    max_frames: int = 0,
) -> int:
    """Renders high-visibility annotated video with bounding boxes, speeds, and TTC alerts."""
    import cv2
    import numpy as np

    tracks_px = load_jsonl(tracks_px_path) if tracks_px_path.exists() else []
    by_frame = defaultdict(list)
    for r in tracks_px:
        by_frame[r["frame"]].append(r)

    events = []
    if events_path.exists():
        events = load_jsonl(events_path)

    # Build lookup of active conflict pairs per frame
    active_conflicts = defaultdict(list)
    for evt in events:
        min_f = evt.get("min_ttc_frame", 0)
        ttc = evt.get("ttc_s", 1.0)
        sev = evt.get("severity", "conflict")
        t_ids = evt.get("track_ids", [])
        # Window of 45 frames around min_ttc_frame
        for f in range(max(0, min_f - 45), min_f + 45):
            active_conflicts[f].append({
                "track_ids": t_ids,
                "ttc_s": ttc,
                "severity": sev,
                "event_id": evt.get("event_id", ""),
            })

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"Warning: could not open video for overlay generation: {video_path}")
        return 0

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    out_video_path.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_video_path), fourcc, fps, (width, height))

    scale = max(width / 1280.0, 0.6)
    frame_no = 0
    written = 0

    valid_region = calib.valid_region_px if calib else None

    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = None

    target_total = max_frames if (max_frames and max_frames < total_frames) else total_frames
    pbar = tqdm(total=target_total, desc="Rendering Annotated Video", unit="frame", dynamic_ncols=True) if tqdm and target_total > 0 else None

    accident_model = None
    accident_model_path = REPO_ROOT / "yolo11x_accident.pt"
    if accident_model_path.exists():
        try:
            from ultralytics import YOLO
            accident_model = YOLO(str(accident_model_path))
        except Exception:
            accident_model = None

    last_visual_crashes = []

    print(f"Rendering annotated visualization to {out_video_path} ...")
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        if max_frames and frame_no >= max_frames:
            break

        # Draw validity polygon if available
        if valid_region:
            poly = np.asarray(valid_region, dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(frame, [poly], True, (0, 200, 255), 2)

        # Visual Accident Detection (AI Vision)
        if accident_model is not None and (frame_no % 2 == 0 or not last_visual_crashes):
            try:
                acc_res = accident_model.predict(frame, conf=0.35, device=0, verbose=False)
                last_visual_crashes = []
                for b in acc_res[0].boxes:
                    if int(b.cls[0]) == 0:  # accident class
                        last_visual_crashes.append({
                            "bbox": [float(v) for v in b.xyxy[0]],
                            "conf": float(b.conf[0])
                        })
            except Exception:
                pass

        # Draw visual accident bounding boxes
        for ac in last_visual_crashes:
            ax1, ay1, ax2, ay2 = (int(v) for v in ac["bbox"])
            cv2.rectangle(frame, (ax1, ay1), (ax2, ay2), (0, 0, 255), 4)
            cv2.putText(
                frame,
                f"💥 CRASH DETECTED ({int(ac['conf']*100)}%)",
                (ax1, max(ay1 - 10, int(30 * scale))),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7 * scale,
                (0, 0, 255),
                max(int(2.5 * scale), 2),
                cv2.LINE_AA,
            )

        conflicts_this_frame = active_conflicts.get(frame_no, [])
        conflicting_tids = set()
        min_ttc_now = None
        for c in conflicts_this_frame:
            for tid in c["track_ids"]:
                conflicting_tids.add(tid)
            if min_ttc_now is None or c["ttc_s"] < min_ttc_now:
                min_ttc_now = c["ttc_s"]

        # Draw vehicle detections
        dets = by_frame.get(frame_no, [])
        for det in dets:
            x1, y1, x2, y2 = (int(v) for v in det["bbox"])
            tid = det.get("track_id", -1)
            is_conflict = tid in conflicting_tids
            colour = severity_colour(min_ttc_now) if is_conflict else CLASS_COLOURS.get(det.get("cls", "car"), (200, 200, 200))
            thickness = 3 if is_conflict else 2

            cv2.rectangle(frame, (x1, y1), (x2, y2), colour, thickness)
            # Draw ground contact point marker
            cv2.drawMarker(frame, ((x1 + x2) // 2, y2), (0, 0, 255), cv2.MARKER_TILTED_CROSS, int(10 * scale), 2)

            label = f"{det.get('cls', 'veh')} #{tid}"
            cv2.putText(
                frame,
                label,
                (x1, max(y1 - 6, int(14 * scale))),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5 * scale,
                colour,
                max(int(2 * scale), 1),
                cv2.LINE_AA,
            )

        # Header status HUD
        hud_bar_h = int(38 * scale)
        cv2.rectangle(frame, (0, 0), (width, hud_bar_h), (15, 23, 42), -1)
        hud_left = f"NETRA AI  |  Frame: {frame_no}/{total_frames}  |  Time: {frame_no / fps:.2f}s  |  Tracked: {len(dets)}"
        cv2.putText(
            frame,
            hud_left,
            (12, int(25 * scale)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6 * scale,
            (248, 250, 252),
            max(int(1.8 * scale), 1),
            cv2.LINE_AA,
        )

        # Conflict / Accident alert banner
        if last_visual_crashes:
            alert_text = f"🚨 CRASH DETECTED (Accident AI {int(last_visual_crashes[0]['conf']*100)}%)"
            alert_color = (0, 0, 255)
            cv2.putText(
                frame,
                alert_text,
                (width - int(520 * scale), int(25 * scale)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.68 * scale,
                alert_color,
                max(int(2.2 * scale), 2),
                cv2.LINE_AA,
            )
        elif conflicts_this_frame:
            if min_ttc_now == 0.0:
                alert_text = "🚨 COLLISION IMPACT DETECTED"
                alert_color = (0, 0, 255)
            elif min_ttc_now is not None and min_ttc_now < 0.8:
                alert_text = f"🚨 SEVERE CONFLICT: TTC {min_ttc_now:.2f}s"
                alert_color = (40, 40, 240)
            elif min_ttc_now is not None:
                alert_text = f"⚠️ NEAR-MISS CONFLICT: TTC {min_ttc_now:.2f}s"
                alert_color = (40, 180, 240)
            else:
                alert_text = "⚠️ CONFLICT RISK"
                alert_color = (40, 180, 240)

            cv2.putText(
                frame,
                alert_text,
                (width - int(460 * scale), int(25 * scale)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.68 * scale,
                alert_color,
                max(int(2.2 * scale), 2),
                cv2.LINE_AA,
            )
        else:
            cv2.putText(
                frame,
                "STATUS: NORMAL FLOW",
                (width - int(320 * scale), int(25 * scale)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55 * scale,
                (74, 222, 128),
                max(int(1.5 * scale), 1),
                cv2.LINE_AA,
            )

        writer.write(frame)
        written += 1
        frame_no += 1
        if pbar is not None:
            pbar.update(1)

    if pbar is not None:
        pbar.close()
    cap.release()
    writer.release()
    print(f"Annotated video saved successfully ({written} frames).")
    return written


def create_auto_calibration(video_path: Path, video_id: str = "auto_road_calib") -> Calibration:
    """Creates a perspective-corrected, full-frame ground homography tailored to the video's resolution."""
    import cv2
    from edge.calibration.homography import solve_homography

    cap = cv2.VideoCapture(str(video_path))
    W = float(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280.0)
    H = float(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720.0)
    cap.release()

    # Perspective road plane: bottom near-field (0m) to upper far-field (40m)
    src = [
        [0.05 * W, H],
        [0.95 * W, H],
        [0.25 * W, 0.25 * H],
        [0.75 * W, 0.25 * H],
    ]
    dst = [
        [-10.0, 0.0],
        [10.0, 0.0],
        [-7.0, 40.0],
        [7.0, 40.0],
    ]
    H_auto = solve_homography(src, dst)
    return Calibration(
        video_id=video_id,
        H=H_auto,
        location=[12.9716, 77.5946],
        valid_region_px=[],  # Full frame coverage without polygon crop
        max_range_m=100.0,
        rms_error_m=0.01,
    )


def run_pipeline_on_video(
    video_path: Path,
    calib_path: Optional[Path] = None,
    out_dir: Path = Path("out"),
    model_weights: str = "yolov8m.pt",
    imgsz: int = 640,
    conf: float = 0.25,
    device: str = "auto",
    threads: int = 0,
    max_frames: int = 0,
    enable_gate: bool = False,
    render_video: bool = True,
) -> Dict[str, Any]:
    """Executes the full end-to-end NETRA analytics pipeline on an input video file."""
    t_start = time.perf_counter()
    out_dir.mkdir(parents=True, exist_ok=True)

    overrides = []
    if model_weights:
        overrides.append(f"detector.weights={model_weights}")
    if imgsz:
        overrides.append(f"detector.imgsz={imgsz}")
    if conf:
        overrides.append(f"detector.conf={conf}")
    if device:
        overrides.append(f"detector.device={device}")

    # 1. Resolve Calibration
    if calib_path and calib_path.exists():
        calib = load_calibration(calib_path)
        calib_type_str = f"Custom Calibration ({calib.video_id})"
    else:
        calib = create_auto_calibration(video_path)
        calib_type_str = "Auto-Adaptive Road Plane (Full Frame)"
        overrides.append("suppression.validity_region=false")

    cfg = load_config("edge/config.yaml", overrides=overrides)
    if threads > 0:
        pin_threads(threads)

    # Detect active hardware device
    try:
        import torch
        gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
        hw_str = f"GPU: {gpu_name} (CUDA Accelerated)" if torch.cuda.is_available() else "CPU"
    except Exception:
        hw_str = "CPU"

    _banner("NETRA VIDEO INGESTION & CONFLICT RISK PIPELINE")
    print(f"  Source Video      : {video_path}")
    print(f"  Output Directory  : {out_dir.resolve()}")
    print(f"  Compute Hardware  : {hw_str}")
    print(f"  Detector Model    : {model_weights}")
    print(f"  Detector Config   : Resolution {imgsz}x{imgsz}, conf={conf}")
    print(f"  Calibration Plane : {calib_type_str}")

    # Output file paths
    tracks_px_path = out_dir / "tracks_px.jsonl"
    tracks_m_path = out_dir / "tracks_m.jsonl"
    events_path = out_dir / "events.jsonl"
    norms_path = out_dir / "norms.json"
    norms_overlay_path = out_dir / "lane_overlay.png"
    annotated_video_path = out_dir / "annotated_video.mp4"
    summary_report_path = out_dir / "pipeline_summary.json"

    # =========================================================================
    # STAGE 1: M2 Vehicle Detection & Tracking (YOLOv8 + ByteTrack)
    # =========================================================================
    _banner(f"STAGE 1: Vehicle Detection & Tracking (M2 - {model_weights} + ByteTrack)")
    from edge.track import run_track

    # Only enforce calibration polygon if user explicitly passed their own calibration file
    # This prevents dropping vehicles on arbitrary new videos
    track_calib_arg = str(calib_path) if (calib_path and calib_path.exists()) else None

    track_ns = argparse.Namespace(
        video=str(video_path),
        out=str(tracks_px_path),
        calib=track_calib_arg,
        overlay=None,
        gate=enable_gate,
        max_frames=max_frames,
        threads=threads,
        config="edge/config.yaml",
        overrides=overrides,
    )
    track_stats = run_track.run(track_ns)
    px_rows = list(read_jsonl(tracks_px_path)) if tracks_px_path.exists() else []
    unique_tracks = len(set(r["track_id"] for r in px_rows if "track_id" in r))

    print(f"Detection & Tracking completed:")
    print(f"  Processed {track_stats.get('frames', 0)} frames at {track_stats.get('fps', 0.0)} FPS")
    print(f"  Active vehicles tracked : {unique_tracks} ({len(px_rows)} detection rows)")
    print(f"  Saved pixel tracks -> {tracks_px_path}")

    # =========================================================================
    # STAGE 2: M1 Ground Projection (Pixels -> Metres & km/h)
    # =========================================================================
    _banner("STAGE 2: Ground Plane Metric Projection (M1 - Homography)")
    if calib is None:
        raise RuntimeError("No valid calibration available for ground projection.")

    m_rows = []
    if px_rows:
        m_rows, proj_stats = project_tracks(px_rows, calib, cfg)
        print(proj_stats.render())
    write_jsonl(
        str(tracks_m_path),
        m_rows,
        header=f"tracks_m.jsonl - ground plane metric data. Calibration: {calib.video_id}",
    )
    print(f"Wrote {len(m_rows)} projected metric detections -> {tracks_m_path}")

    # =========================================================================
    # STAGE 3: M3 Near-Miss Conflict Analysis (TTC / PET & Suppression)
    # =========================================================================
    _banner("STAGE 3: Near-Miss Conflict Engine (M3 - TTC, PET, Suppression)")
    events = []
    if m_rows:
        engine = ConflictEngine(cfg, calib)
        events = engine.run(m_rows)
        print(engine.stats.render(engine.suppression))

    events_dict_list = [e.to_dict() for e in events]
    write_jsonl(
        str(events_path),
        events_dict_list,
        header="events.jsonl - NETRA ConflictEvent records",
    )
    print(f"Wrote {len(events)} conflict events -> {events_path}")

    severe_count = sum(1 for e in events if e.severity == "severe")
    conflict_count = len(events) - severe_count

    # =========================================================================
    # STAGE 4: M5 Self-Calibrating Road Norms
    # =========================================================================
    _banner("STAGE 4: Self-Calibrating Road Norms (M5 - V85 Speed, Lanes, Flow)")
    norms_data = run_norms_pipeline(m_rows) if len(m_rows) > 0 else {
        "speed_85_kmh": None,
        "sample_size": 0,
        "lanes": [],
        "signal_cycle_s": None,
    }
    with open(norms_path, "w", encoding="utf-8") as f:
        json.dump(norms_data, f, indent=2)
    print(f"Road Norms Learned:")
    print(f"  Civil Speed Limit (V85) : {norms_data.get('speed_85_kmh')} km/h (sample: {norms_data.get('sample_size')} tracks)")
    print(f"  Discovered Lanes Count  : {len(norms_data.get('lanes', []))}")
    print(f"  Signal Cycle Period     : {norms_data.get('signal_cycle_s')} s")
    print(f"Saved road norms -> {norms_path}")

    if len(m_rows) > 0:
        try:
            render_lane_overlay(m_rows, norms_data, norms_overlay_path)
        except Exception as e:
            print(f"Notice: Lane overlay image render skipped: {e}")

    # =========================================================================
    # STAGE 5: Annotated Visualization Video
    # =========================================================================
    if render_video:
        _banner("STAGE 5: Annotated Video Rendering (Bounding Boxes, Speeds, TTC)")
        generate_annotated_video(
            video_path=video_path,
            tracks_px_path=tracks_px_path,
            events_path=events_path,
            out_video_path=annotated_video_path,
            calib=calib,
            max_frames=max_frames,
        )
    # =========================================================================
    # STAGE 6: M7 Edge Emission & Server Sync
    # =========================================================================
    if len(events) > 0:
        try:
            import urllib.request
            payload = json.dumps([e.to_dict() for e in events]).encode('utf-8')
            req = urllib.request.Request(
                "http://localhost:8000/api/events",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    print(f"Synced {len(events)} conflict events to live NETRA API (http://localhost:8000)")
        except Exception:
            pass

    total_wall_s = time.perf_counter() - t_start

    # =========================================================================
    # SUMMARY REPORT
    # =========================================================================
    summary = {
        "status": "SUCCESS",
        "video": str(video_path),
        "compute_hardware": hw_str,
        "detector_model": model_weights,
        "resolution": f"{imgsz}x{imgsz}",
        "confidence_threshold": conf,
        "total_runtime_s": round(total_wall_s, 2),
        "tracking_metrics": {
            "frames_processed": track_stats.get("frames", 0),
            "tracking_fps": track_stats.get("fps", 0.0),
            "fps_per_core": track_stats.get("fps_per_core", 0.0),
            "total_tracks": unique_tracks,
        },
        "conflict_analytics": {
            "total_events": len(events),
            "severe_events": severe_count,
            "moderate_conflict_events": conflict_count,
            "min_ttc_observed_s": min((e.ttc_s for e in events), default=None),
        },
        "road_norms": {
            "v85_speed_limit_kmh": norms_data.get("speed_85_kmh"),
            "completed_tracks_sample": norms_data.get("sample_size"),
            "discovered_lanes_count": len(norms_data.get("lanes", [])),
        },
        "output_artifacts": {
            "pixel_tracks": str(tracks_px_path.resolve()),
            "metric_tracks": str(tracks_m_path.resolve()),
            "conflict_events": str(events_path.resolve()),
            "road_norms": str(norms_path.resolve()),
            "annotated_video": str(annotated_video_path.resolve()) if render_video else None,
            "lane_overlay_plot": str(norms_overlay_path.resolve()) if norms_overlay_path.exists() else None,
        },
    }

    with open(summary_report_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    _banner("EXECUTIVE SUMMARY & RESULTS")
    print(f"  Compute Hardware      : {hw_str}")
    print(f"  Model Architecture    : {model_weights}")
    print(f"  Processed Frames      : {track_stats.get('frames', 0)} ({imgsz}x{imgsz})")
    print(f"  Inference Speed       : {track_stats.get('fps', 0.0)} FPS")
    print(f"  Total Runtime         : {total_wall_s:.1f} seconds ({total_wall_s / 60:.2f} mins)")
    print(f"  Vehicles Tracked      : {unique_tracks}")
    print(f"  V85 Speed Limit (Norm): {norms_data.get('speed_85_kmh')} km/h")
    print(f"  Total Near-Misses     : {len(events)}")
    print(f"    - Severe (TTC<0.8s) : {severe_count}")
    print(f"    - Moderate (TTC<1.5s): {conflict_count}")
    if events:
        print(f"    - Min TTC Encounter : {min(e.ttc_s for e in events):.2f} s")
    print()
    print("  Artifacts Generated:")
    print(f"    - Conflict Events   : {events_path}")
    print(f"    - Metric Tracks     : {tracks_m_path}")
    print(f"    - Road Norms        : {norms_path}")
    if render_video:
        print(f"    - Annotated Video   : {annotated_video_path}")
    print(f"    - Summary JSON      : {summary_report_path}")
    print("=" * 72)
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="NETRA: Automated Video Ingestion & Near-Miss Analytics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--video", default=None, help="Path to input video (.mp4, .avi, .mkv)")
    parser.add_argument("--model", default="yolov8m.pt", help="YOLO model weights (yolov8m.pt, yolov8x.pt, yolov8l.pt, default: yolov8m.pt)")
    parser.add_argument("--device", default="auto", help="Compute device (0, cuda, cpu, auto. Default: auto)")
    parser.add_argument("--calib", default=None, help="Path to custom calibration.json (optional)")
    parser.add_argument("--outdir", default="out", help="Directory to store outputs (default: out)")
    parser.add_argument("--imgsz", type=int, default=640, help="YOLO inference size (default: 640)")
    parser.add_argument("--conf", type=float, default=0.25, help="Detection confidence threshold (default: 0.25)")
    parser.add_argument("--threads", type=int, default=0, help="CPU threads to pin (0 = unrestricted, default: 0)")
    parser.add_argument("--max-frames", type=int, default=0, help="Max frames to process (0 = entire video)")
    parser.add_argument("--gate", action="store_true", help="Enable optical motion gate")
    parser.add_argument("--no-overlay", action="store_true", help="Disable rendering the annotated MP4 video")
    args = parser.parse_args()

    video_input = args.video
    if not video_input:
        print("\n=== NETRA Video Ingestion ===")
        video_input = input("Enter path to video file (.mp4, .avi, .mkv): ").strip().strip('"').strip("'")
        if not video_input:
            print("Error: No video file path provided.", file=sys.stderr)
            return 1

    video_path = Path(video_input)
    if not video_path.exists():
        print(f"Error: Video file not found: {video_path}", file=sys.stderr)
        return 1

    calib_path = Path(args.calib) if args.calib else None
    out_dir = Path(args.outdir)

    run_pipeline_on_video(
        video_path=video_path,
        calib_path=calib_path,
        out_dir=out_dir,
        model_weights=args.model,
        imgsz=args.imgsz,
        conf=args.conf,
        device=args.device,
        threads=args.threads,
        max_frames=args.max_frames,
        enable_gate=args.gate,
        render_video=not args.no_overlay,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
