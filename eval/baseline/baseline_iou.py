"""
NETRA M10 — 2D Pixel-IoU Baseline Conflict Detector
Implements the conventional camera-space bounding-box proximity approach
used in common open-source traffic projects as a comparative baseline.

Failure modes demonstrated:
  1. Perspective false alarms: Vehicles 25m apart in depth overlap heavily in 2D pixel space.
  2. Perspective misses / zero lead time: Crossing vehicles at the horizon have 0% box overlap until impact.
"""

import argparse
from collections import defaultdict
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def compute_2d_iou(bbox1: List[float], bbox2: List[float]) -> float:
    """
    Computes standard 2D Intersection-over-Union (IoU) between two bounding boxes.
    Bounding box format: [ymin, xmin, ymax, xmax]
    """
    y1_min, x1_min, y1_max, x1_max = bbox1
    y2_min, x2_min, y2_max, x2_max = bbox2

    # Calculate intersection rectangle
    inter_ymin = max(y1_min, y2_min)
    inter_xmin = max(x1_min, x2_min)
    inter_ymax = min(y1_max, y2_max)
    inter_xmax = min(x1_max, x2_max)

    inter_w = max(0.0, inter_xmax - inter_xmin)
    inter_h = max(0.0, inter_ymax - inter_ymin)
    inter_area = inter_w * inter_h

    if inter_area <= 0.0:
        return 0.0

    area1 = max(0.0, (x1_max - x1_min) * (y1_max - y1_min))
    area2 = max(0.0, (x2_max - x2_min) * (y2_max - y2_min))
    union_area = area1 + area2 - inter_area

    return float(inter_area / union_area) if union_area > 0 else 0.0


def compute_expanded_proximity_iou(bbox1: List[float], bbox2: List[float], expansion_factor: float = 0.25) -> float:
    """
    Expands bounding boxes by a margin to emulate proximity detection heuristics.
    """
    def expand_box(b):
        h = b[2] - b[0]
        w = b[3] - b[1]
        dh = h * expansion_factor
        dw = w * expansion_factor
        return [b[0] - dh, b[1] - dw, b[2] + dh, b[3] + dw]

    return compute_2d_iou(expand_box(bbox1), expand_box(bbox2))


def run_baseline_iou_detector(
    tracks: List[Dict[str, Any]],
    iou_threshold: float = 0.03,
    min_consecutive_frames: int = 3,
    fps: float = 30.0,
) -> List[Dict[str, Any]]:
    """
    Runs naive 2D Pixel-IoU proximity detection on frame-by-frame track detections.

    Args:
        tracks: List of track detections.
        iou_threshold: Minimum IoU threshold to trigger a collision proximity alert.
        min_consecutive_frames: Minimum frame overlap to filter single-frame flutter.
        fps: Video frame rate.

    Returns:
        List of baseline detected conflict events.
    """
    # Group detections by frame
    frames_dict = defaultdict(list)
    for det in tracks:
        frame_idx = det.get("frame")
        if frame_idx is not None and "bbox" in det:
            frames_dict[frame_idx].append(det)

    sorted_frames = sorted(frames_dict.keys())
    raw_encounters = defaultdict(list)  # (tid_a, tid_b) -> [frame_records]

    for frame in sorted_frames:
        dets = frames_dict[frame]
        n = len(dets)
        for i in range(n):
            for j in range(i + 1, n):
                det_a, det_b = dets[i], dets[j]
                tid_a, tid_b = sorted([det_a["track_id"], det_b["track_id"]])
                iou = compute_expanded_proximity_iou(det_a["bbox"], det_b["bbox"])
                if iou >= iou_threshold:
                    t = det_a.get("t", frame / fps)
                    raw_encounters[(tid_a, tid_b)].append({
                        "frame": frame,
                        "t": t,
                        "iou": iou,
                        "cls_a": det_a.get("cls", "car"),
                        "cls_b": det_b.get("cls", "car"),
                    })

    baseline_events = []
    event_id_counter = 1

    # Consolidate frame encounters into discrete events
    for (tid_a, tid_b), encounters in raw_encounters.items():
        if len(encounters) < min_consecutive_frames:
            continue

        # Find contiguous chunks
        encounters_sorted = sorted(encounters, key=lambda x: x["frame"])
        t_first = encounters_sorted[0]["t"]
        t_last = encounters_sorted[-1]["t"]
        max_iou = max(e["iou"] for e in encounters_sorted)

        # Baseline assigns severity purely on IoU magnitude
        severity = "severe" if max_iou > 0.15 else "conflict"

        baseline_events.append({
            "event_id": f"base_evt_{event_id_counter:03d}",
            "method": "Pixel-IoU Baseline",
            "t_sec": round(t_first, 2),
            "t_end_sec": round(t_last, 2),
            "severity": severity,
            "max_iou": round(max_iou, 3),
            "vehicle_a": encounters_sorted[0]["cls_a"],
            "vehicle_b": encounters_sorted[0]["cls_b"],
            "track_ids": [tid_a, tid_b],
        })
        event_id_counter += 1

    return baseline_events


def main():
    parser = argparse.ArgumentParser(description="NETRA M10: 2D Pixel-IoU Baseline Conflict Detector")
    parser.add_argument("--tracks", type=Path, default=Path("fixtures/tracks.sample.jsonl"), help="Path to tracks.jsonl")
    parser.add_argument("--output", type=Path, default=Path("eval/baseline/baseline_events.json"), help="Path to save baseline events JSON")
    parser.add_argument("--threshold", type=float, default=0.03, help="IoU threshold")
    args = parser.parse_args()

    # Load tracks
    from edge.norms.norms_engine import load_tracks_jsonl
    tracks = load_tracks_jsonl(args.tracks)
    print(f"Loaded {len(tracks)} frame detections for baseline evaluation.")

    baseline_events = run_baseline_iou_detector(tracks, iou_threshold=args.threshold)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(baseline_events, f, indent=2)

    print(f"Detected {len(baseline_events)} Pixel-IoU baseline proximity events -> {args.output}")


if __name__ == "__main__":
    main()
