"""
NETRA M5 — Against-Flow Direction Classifier
Determines whether a vehicle's motion aligns with the natural learned traffic flow,
or violates road discipline by travelling against the flow (wrong-way driving).
"""

import math
from typing import Any, Dict, List, Tuple
import numpy as np


def compute_heading_deg(dx: float, dy: float) -> float:
    """Computes heading angle in degrees [0, 360) from velocity/displacement components."""
    heading_rad = math.atan2(dy, dx)
    return math.degrees(heading_rad) % 360.0


def angular_difference_deg(angle1_deg: float, angle2_deg: float) -> float:
    """Computes the shortest absolute difference between two angles in degrees [0, 180]."""
    diff = abs((angle1_deg - angle2_deg + 180.0) % 360.0 - 180.0)
    return diff


def point_to_polyline_distance(point: np.ndarray, polyline: np.ndarray) -> float:
    """Calculates the minimum distance in metres from a point to a 2D polyline."""
    if len(polyline) < 2:
        return float(np.linalg.norm(point - polyline[0])) if len(polyline) == 1 else float("inf")

    min_dist = float("inf")
    p = np.array(point)

    for i in range(len(polyline) - 1):
        a = np.array(polyline[i])
        b = np.array(polyline[i + 1])
        ab = b - a
        ab_len_sq = np.dot(ab, ab)
        if ab_len_sq == 0:
            dist = np.linalg.norm(p - a)
        else:
            t = max(0.0, min(1.0, np.dot(p - a, ab) / ab_len_sq))
            projection = a + t * ab
            dist = np.linalg.norm(p - projection)
        if dist < min_dist:
            min_dist = dist

    return float(min_dist)


def classify_track_flow(
    track_records: List[Dict[str, Any]],
    lanes: List[Dict[str, Any]],
    angle_threshold_deg: float = 110.0,
    max_lane_distance_m: float = 8.0,
) -> Dict[str, Any]:
    """
    Evaluates a vehicle track's direction against the learned lane geometry.

    Args:
        track_records: List of frame observations for a single track.
        lanes: Learned lane definitions from M5 (each having centreline_m and heading_deg).
        angle_threshold_deg: Max angular divergence before flagging as 'against flow' (default: 110 deg).
        max_lane_distance_m: Maximum distance to associate track with a lane.

    Returns:
        Dict with:
          - 'direction': 'normal' | 'against flow' | 'unknown'
          - 'matched_lane_id': int or None
          - 'heading_deg': float
          - 'angular_deviation_deg': float
    """
    if len(track_records) < 2 or not lanes:
        return {
            "direction": "normal",
            "matched_lane_id": None,
            "heading_deg": None,
            "angular_deviation_deg": 0.0,
        }

    # Extract coordinates sorted by timestamp
    sorted_recs = sorted(track_records, key=lambda r: r.get("t", 0))
    points = [r["ground_m"] for r in sorted_recs if "ground_m" in r and len(r["ground_m"]) == 2]

    if len(points) < 2:
        return {
            "direction": "normal",
            "matched_lane_id": None,
            "heading_deg": None,
            "angular_deviation_deg": 0.0,
        }

    p_start = np.array(points[0])
    p_end = np.array(points[-1])
    dx = float(p_end[0] - p_start[0])
    dy = float(p_end[1] - p_start[1])
    disp = math.hypot(dx, dy)

    if disp < 1.5:  # Static or micro-movements
        return {
            "direction": "normal",
            "matched_lane_id": None,
            "heading_deg": None,
            "angular_deviation_deg": 0.0,
        }

    track_heading_deg = compute_heading_deg(dx, dy)

    # Find the nearest lane based on mid-point or average trajectory distance
    p_mid = np.mean(points, axis=0)
    best_lane = None
    min_lane_dist = float("inf")

    for lane in lanes:
        centreline = np.array(lane["centreline_m"])
        dist = point_to_polyline_distance(p_mid, centreline)
        if dist < min_lane_dist:
            min_lane_dist = dist
            best_lane = lane

    if best_lane is None or min_lane_dist > max_lane_distance_m:
        # If too far from any learned lane, default to normal
        return {
            "direction": "normal",
            "matched_lane_id": None,
            "heading_deg": round(track_heading_deg, 1),
            "angular_deviation_deg": 0.0,
        }

    lane_heading_deg = best_lane["heading_deg"]
    dev_deg = angular_difference_deg(track_heading_deg, lane_heading_deg)

    is_against_flow = dev_deg >= angle_threshold_deg
    direction_label = "against flow" if is_against_flow else "normal"

    return {
        "direction": direction_label,
        "matched_lane_id": best_lane["id"],
        "heading_deg": round(track_heading_deg, 1),
        "lane_heading_deg": lane_heading_deg,
        "angular_deviation_deg": round(dev_deg, 1),
        "distance_to_lane_m": round(min_lane_dist, 2),
    }
