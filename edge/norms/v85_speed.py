"""
NETRA M5 — 85th-Percentile Speed (V85) Calculator
Computes self-calibrating road speed limits using standard civil traffic engineering practice:
The speed below which 85% of vehicles travel under free-flowing conditions.
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple
import numpy as np


def extract_track_speeds(
    tracks: List[Dict[str, Any]],
    min_frames: int = 5,
    min_speed_kmh: float = 3.0,
    max_speed_kmh: float = 150.0,
) -> Tuple[List[float], Dict[int, float]]:
    """
    Extracts representative speeds for each valid unique vehicle track.

    Args:
        tracks: List of track detection dicts (PRD Section 5.2 format).
        min_frames: Minimum frame observations required (tracks < 5 are dropped per PRD).
        min_speed_kmh: Threshold to exclude static/parked vehicles.
        max_speed_kmh: Upper threshold to reject tracker glitch spikes (>150 km/h per PRD).

    Returns:
        Tuple of (list_of_all_valid_speeds, dict_of_track_id_to_median_speed)
    """
    # Group detections by track_id
    track_observations = defaultdict(list)
    for record in tracks:
        track_id = record.get("track_id")
        if track_id is None:
            continue
        track_observations[track_id].append(record)

    valid_speeds: List[float] = []
    track_speed_map: Dict[int, float] = {}

    for track_id, obs in track_observations.items():
        # Discard short tracks (PRD: tracks shorter than 5 frames indicate tracking failure)
        if len(obs) < min_frames:
            continue

        instantaneous_speeds = []
        for det in obs:
            v_mps = det.get("v_mps")
            if v_mps and len(v_mps) == 2:
                # v_mps = [vx, vy] on ground plane
                speed_mps = np.hypot(v_mps[0], v_mps[1])
                speed_kmh = speed_mps * 3.6
                if min_speed_kmh <= speed_kmh <= max_speed_kmh:
                    instantaneous_speeds.append(speed_kmh)
            elif "ground_m" in det:
                # Fallback if v_mps is missing but ground coords are present
                pass

        # If instantaneous velocities weren't precomputed, compute from ground displacement
        if not instantaneous_speeds and len(obs) >= 2:
            obs_sorted = sorted(obs, key=lambda x: x.get("t", 0))
            t0, t1 = obs_sorted[0].get("t", 0), obs_sorted[-1].get("t", 0)
            dt = t1 - t0
            if dt > 0 and "ground_m" in obs_sorted[0] and "ground_m" in obs_sorted[-1]:
                p0 = np.array(obs_sorted[0]["ground_m"])
                p1 = np.array(obs_sorted[-1]["ground_m"])
                dist_m = float(np.linalg.norm(p1 - p0))
                disp_speed_kmh = (dist_m / dt) * 3.6
                if min_speed_kmh <= disp_speed_kmh <= max_speed_kmh:
                    instantaneous_speeds.append(disp_speed_kmh)

        if instantaneous_speeds:
            # Use median speed to be robust against occasional frame-level tracker jitter
            median_speed = float(np.median(instantaneous_speeds))
            valid_speeds.append(median_speed)
            track_speed_map[track_id] = round(median_speed, 2)

    return valid_speeds, track_speed_map


def compute_v85(
    speeds_kmh: List[float],
    min_sample_size: int = 200,
) -> Dict[str, Any]:
    """
    Calculates the 85th-percentile speed (V85) and statistical metadata.

    Args:
        speeds_kmh: List of vehicle speeds in km/h.
        min_sample_size: Required sample size per PRD (>=200 tracks for certified V85).

    Returns:
        Dictionary containing speed_85_kmh, sample_size, and diagnostics.
    """
    sample_size = len(speeds_kmh)

    if sample_size == 0:
        return {
            "speed_85_kmh": None,
            "sample_size": 0,
            "statistically_sound": False,
            "mean_speed_kmh": None,
            "p50_speed_kmh": None,
            "min_speed_kmh": None,
            "max_speed_kmh": None,
        }

    v85 = float(np.percentile(speeds_kmh, 85))
    v50 = float(np.percentile(speeds_kmh, 50))
    v_mean = float(np.mean(speeds_kmh))
    v_min = float(np.min(speeds_kmh))
    v_max = float(np.max(speeds_kmh))

    return {
        "speed_85_kmh": round(v85, 1),
        "sample_size": sample_size,
        "statistically_sound": sample_size >= min_sample_size,
        "mean_speed_kmh": round(v_mean, 1),
        "p50_speed_kmh": round(v50, 1),
        "min_speed_kmh": round(v_min, 1),
        "max_speed_kmh": round(v_max, 1),
    }
