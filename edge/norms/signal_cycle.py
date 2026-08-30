"""
NETRA M5 — Traffic Signal Cycle Estimator
Detects traffic light cycle periodicity from vehicle arrival bunching across stop-lines.
Gracefully returns null when duration is insufficient or intersection is unsignalized.
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional
import numpy as np


def estimate_signal_cycle(
    tracks: List[Dict[str, Any]],
    min_observation_seconds: float = 120.0,
    min_cycle_s: float = 30.0,
    max_cycle_s: float = 180.0,
    crossing_line_x: float = 30.0,
) -> Optional[float]:
    """
    Estimates the signal cycle length from vehicle arrival bunching patterns.

    Args:
        tracks: List of track detection records.
        min_observation_seconds: Minimum clip duration needed for periodic analysis.
        min_cycle_s: Minimum plausible traffic light cycle (seconds).
        max_cycle_s: Maximum plausible traffic light cycle (seconds).
        crossing_line_x: Virtual stop line X-coordinate in ground metres.

    Returns:
        Estimated cycle duration in seconds (rounded to 1 decimal place), or None.
    """
    # Group by track_id
    track_obs = defaultdict(list)
    for det in tracks:
        tid = det.get("track_id")
        if tid is not None and "ground_m" in det:
            track_obs[tid].append(det)

    if not track_obs:
        return None

    # Find crossing timestamps
    crossing_times = []
    min_t, max_t = float("inf"), float("-inf")

    for tid, obs in track_obs.items():
        sorted_obs = sorted(obs, key=lambda x: x.get("t", 0))
        for i in range(len(sorted_obs) - 1):
            t_curr = sorted_obs[i].get("t", 0)
            t_next = sorted_obs[i + 1].get("t", 0)
            min_t = min(min_t, t_curr)
            max_t = max(max_t, t_next)

            x_curr = sorted_obs[i]["ground_m"][0]
            x_next = sorted_obs[i + 1]["ground_m"][0]

            # Check if crossing line was crossed
            if (x_curr <= crossing_line_x <= x_next) or (x_next <= crossing_line_x <= x_curr):
                t_cross = t_curr + (crossing_line_x - x_curr) / (x_next - x_curr + 1e-6) * (t_next - t_curr)
                crossing_times.append(t_cross)
                break

    duration = max_t - min_t
    if duration < min_observation_seconds or len(crossing_times) < 20:
        return None

    # Bin arrivals into 1-second bins
    time_bins = int(duration) + 1
    arrival_counts, _ = np.histogram(crossing_times, bins=time_bins, range=(min_t, min_t + time_bins))

    # Center the signal
    signal = arrival_counts - np.mean(arrival_counts)

    # Compute Auto-Correlation Function (ACF)
    acf = np.correlate(signal, signal, mode="full")
    acf = acf[len(acf) // 2 :]  # Non-negative lags only
    if acf[0] > 0:
        acf = acf / acf[0]

    # Search for peak in [min_cycle_s, max_cycle_s]
    min_lag = int(min_cycle_s)
    max_lag = min(int(max_cycle_s), len(acf) - 2)

    if max_lag <= min_lag:
        return None

    search_window = acf[min_lag:max_lag]
    if len(search_window) < 3:
        return None

    peak_idx = int(np.argmax(search_window))
    peak_val = search_window[peak_idx]
    best_lag = min_lag + peak_idx

    # Check peak prominence
    # If peak correlation is strong (> 0.25) and higher than neighbors
    if peak_val > 0.25 and 0 < peak_idx < len(search_window) - 1:
        if peak_val > search_window[peak_idx - 1] and peak_val > search_window[peak_idx + 1]:
            return round(float(best_lag), 1)

    return None
