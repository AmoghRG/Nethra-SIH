"""
NETRA M5 — Trajectory Clustering & Lane Geometry Extractor
Discovers physical road lanes by unsupervised clustering of continuous vehicle paths,
extracting centrelines and dominant directional headings.
"""

from collections import defaultdict
import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from sklearn.cluster import DBSCAN


def resample_trajectory(points: np.ndarray, num_points: int = 10) -> Optional[np.ndarray]:
    """
    Resamples a 2D polyline trajectory into a fixed number of equidistant waypoints.

    Args:
        points: (K, 2) array of [x, y] coordinates in ground metres.
        num_points: Target number of evenly spaced points.

    Returns:
        (num_points, 2) array of resampled coordinates, or None if invalid.
    """
    if len(points) < 2:
        return None

    # Calculate cumulative distance along path
    dists = np.sqrt(np.sum(np.diff(points, axis=0) ** 2, axis=1))
    cum_dist = np.insert(np.cumsum(dists), 0, 0.0)
    total_length = cum_dist[-1]

    if total_length < 2.0:  # Ignore paths with near-zero displacement (< 2 metres)
        return None

    target_distances = np.linspace(0.0, total_length, num_points)
    resampled_x = np.interp(target_distances, cum_dist, points[:, 0])
    resampled_y = np.interp(target_distances, cum_dist, points[:, 1])

    return np.column_stack((resampled_x, resampled_y))


def extract_lane_clusters(
    tracks: List[Dict[str, Any]],
    min_track_points: int = 5,
    resample_count: int = 10,
    eps: float = 4.5,
    min_samples: int = 3,
) -> Tuple[List[Dict[str, Any]], Dict[int, int]]:
    """
    Clusters vehicle tracks to extract physical road lanes and dominant directions.

    Args:
        tracks: List of track detections (PRD §5.2).
        min_track_points: Min observations per track.
        resample_count: Waypoints per resampled path for feature vectors.
        eps: DBSCAN epsilon distance parameter in feature space (metres).
        min_samples: Min tracks to form a valid recognized lane.

    Returns:
        Tuple of (list_of_lanes, dict_of_track_id_to_lane_id)
    """
    # Group detections by track_id
    track_obs = defaultdict(list)
    for det in tracks:
        tid = det.get("track_id")
        if tid is not None and "ground_m" in det:
            track_obs[tid].append(det)

    valid_track_ids = []
    feature_vectors = []
    resampled_paths = []

    for tid, obs in track_obs.items():
        if len(obs) < min_track_points:
            continue

        # Sort chronologically
        obs_sorted = sorted(obs, key=lambda x: x.get("t", 0))
        pts = np.array([o["ground_m"] for o in obs_sorted if len(o.get("ground_m", [])) == 2])
        if len(pts) < min_track_points:
            continue

        resampled = resample_trajectory(pts, num_points=resample_count)
        if resampled is not None:
            valid_track_ids.append(tid)
            resampled_paths.append(resampled)
            # Flatten to 1D vector (resample_count * 2)
            feature_vectors.append(resampled.flatten())

    if not feature_vectors:
        return [], {}

    X = np.array(feature_vectors)

    # Cluster using DBSCAN
    clustering = DBSCAN(eps=eps, min_samples=min_samples).fit(X)
    labels = clustering.labels_

    lanes: List[Dict[str, Any]] = []
    track_lane_map: Dict[int, int] = {}

    unique_labels = sorted(list(set(labels)))
    lane_id_counter = 0

    for label in unique_labels:
        if label == -1:
            # Noise / outliers
            continue

        cluster_indices = np.where(labels == label)[0]
        cluster_paths = [resampled_paths[i] for i in cluster_indices]

        for i in cluster_indices:
            track_lane_map[valid_track_ids[i]] = lane_id_counter

        # Compute median centreline across all trajectories in this cluster
        stacked_paths = np.array(cluster_paths)  # shape: (N, resample_count, 2)
        mean_centreline = np.mean(stacked_paths, axis=0)  # shape: (resample_count, 2)

        # Simplify centreline to 3-5 key reference points
        num_waypoints = min(5, resample_count)
        waypoint_indices = np.linspace(0, resample_count - 1, num_waypoints, dtype=int)
        simplified_centreline = [
            [round(float(mean_centreline[idx, 0]), 2), round(float(mean_centreline[idx, 1]), 2)]
            for idx in waypoint_indices
        ]

        # Compute dominant heading in degrees (0 = East, 90 = North, 180 = West, 270 = South)
        p_start = mean_centreline[0]
        p_end = mean_centreline[-1]
        dx = p_end[0] - p_start[0]
        dy = p_end[1] - p_start[1]
        heading_rad = math.atan2(dy, dx)
        heading_deg = math.degrees(heading_rad) % 360.0

        lanes.append({
            "id": lane_id_counter,
            "centreline_m": simplified_centreline,
            "heading_deg": round(heading_deg, 1),
            "sample_count": len(cluster_indices),
        })

        lane_id_counter += 1

    return lanes, track_lane_map
