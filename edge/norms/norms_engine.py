"""
NETRA M5 — Self-Calibrating Road Norms Engine
Main execution pipeline for Module M5.
Learns V85 speed limits, lane geometry, flow directions, and signal cycles from track observations.
Exports `norms.json` (PRD §5.4) and renders `lane_overlay.png`.
"""

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import matplotlib.pyplot as plt
import numpy as np

from edge.norms.v85_speed import compute_v85, extract_track_speeds
from edge.norms.lane_clustering import extract_lane_clusters
from edge.norms.flow_classifier import classify_track_flow
from edge.norms.signal_cycle import estimate_signal_cycle


def load_tracks_jsonl(file_path: Path) -> List[Dict[str, Any]]:
    """Loads track detection records from a JSONL file."""
    records = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def run_norms_pipeline(
    tracks: List[Dict[str, Any]],
    min_sample_size: int = 200,
) -> Dict[str, Any]:
    """
    Executes the full self-calibrating road norms pipeline on vehicle tracks.

    Returns:
        Structured norms dictionary matching PRD Section 5.4.
    """
    # 1. 85th-Percentile Speed
    speeds, track_speed_map = extract_track_speeds(tracks)
    v85_result = compute_v85(speeds, min_sample_size=min_sample_size)

    # 2. Lane Clustering & Centrelines
    lanes, track_lane_map = extract_lane_clusters(tracks)

    # 3. Flow Direction Classification across all tracks
    from collections import defaultdict
    track_obs = defaultdict(list)
    for det in tracks:
        tid = det.get("track_id")
        if tid is not None:
            track_obs[tid].append(det)

    against_flow_count = 0
    total_classified_tracks = 0
    for tid, obs in track_obs.items():
        flow_res = classify_track_flow(obs, lanes)
        if flow_res["direction"] == "against flow":
            against_flow_count += 1
        if flow_res["matched_lane_id"] is not None:
            total_classified_tracks += 1

    # 4. Signal Cycle Estimation
    signal_cycle = estimate_signal_cycle(tracks)

    # 5. Format strictly according to Section 5.4 Schema
    # Strip internal sample_count from lanes to ensure 100% schema match
    clean_lanes = [
        {
            "id": lane["id"],
            "centreline_m": lane["centreline_m"],
            "heading_deg": lane["heading_deg"],
        }
        for lane in lanes
    ]

    norms_output = {
        "speed_85_kmh": v85_result["speed_85_kmh"],
        "sample_size": v85_result["sample_size"],
        "lanes": clean_lanes,
        "signal_cycle_s": signal_cycle,
    }

    return norms_output


def render_lane_overlay(
    tracks: List[Dict[str, Any]],
    norms_data: Dict[str, Any],
    output_image_path: Path,
) -> None:
    """
    Renders an engineering diagnostic visualization of the learned road norms:
    - Raw trajectories (faded)
    - Learned lane centrelines with directional arrows
    - Against-flow violations highlighted in red
    - Statistical HUD card with V85 speed and sample metrics
    """
    from collections import defaultdict
    track_obs = defaultdict(list)
    for det in tracks:
        tid = det.get("track_id")
        if tid is not None and "ground_m" in det:
            track_obs[tid].append(det)

    lanes = norms_data.get("lanes", [])
    fig, ax = plt.subplots(figsize=(12, 8), facecolor="#0f172a")
    ax.set_facecolor("#1e293b")

    # Plot raw trajectories
    for tid, obs in track_obs.items():
        sorted_obs = sorted(obs, key=lambda x: x.get("t", 0))
        pts = np.array([o["ground_m"] for o in sorted_obs if len(o.get("ground_m", [])) == 2])
        if len(pts) < 3:
            continue

        flow_info = classify_track_flow(obs, lanes)
        if flow_info["direction"] == "against flow":
            # Highlight wrong-way violations in red
            ax.plot(pts[:, 0], pts[:, 1], color="#ef4444", alpha=0.8, linewidth=1.8, linestyle="--", label="Against Flow Track" if "Against Flow Track" not in ax.get_legend_handles_labels()[1] else "")
        else:
            ax.plot(pts[:, 0], pts[:, 1], color="#64748b", alpha=0.25, linewidth=0.8)

    # Plot learned lane centrelines
    colors = ["#38bdf8", "#4ade80", "#fbbf24", "#f472b6", "#a78bfa"]
    for i, lane in enumerate(lanes):
        c_pts = np.array(lane["centreline_m"])
        color = colors[i % len(colors)]
        lane_label = f"Lane {lane['id']} ({lane['heading_deg']}°)"
        ax.plot(c_pts[:, 0], c_pts[:, 1], color=color, linewidth=3.5, label=lane_label)
        ax.scatter(c_pts[:, 0], c_pts[:, 1], color=color, s=50, edgecolors="#ffffff", zorder=5)

        # Add directional flow arrow at midpoint
        if len(c_pts) >= 2:
            mid_idx = len(c_pts) // 2
            p0 = c_pts[mid_idx - 1]
            p1 = c_pts[mid_idx]
            dx = p1[0] - p0[0]
            dy = p1[1] - p0[1]
            length = np.hypot(dx, dy)
            if length > 0:
                ax.annotate(
                    "",
                    xy=(p1[0], p1[1]),
                    xytext=(p0[0], p0[1]),
                    arrowprops=dict(arrowstyle="->,head_width=0.6,head_length=0.8", color=color, lw=2.5),
                )

    ax.set_title("NETRA M5 — Self-Calibrating Road Norms & Trajectory Clusters", color="#f8fafc", fontsize=14, fontweight="bold", pad=15)
    ax.set_xlabel("Ground X Coordinate (metres)", color="#94a3b8", fontsize=11)
    ax.set_ylabel("Ground Y Coordinate (metres)", color="#94a3b8", fontsize=11)
    ax.tick_params(colors="#94a3b8")
    ax.grid(True, linestyle=":", color="#334155", alpha=0.6)

    # Statistical HUD Box
    v85 = norms_data.get("speed_85_kmh")
    sample_size = norms_data.get("sample_size", 0)
    sig_cycle = norms_data.get("signal_cycle_s")
    sig_str = f"{sig_cycle} s" if sig_cycle is not None else "Unsignalized / Null"

    hud_text = (
        f"Civil Speed Limit (V85): {v85} km/h\n"
        f"Completed Tracks Sample: {sample_size}\n"
        f"Learned Lanes Count: {len(lanes)}\n"
        f"Signal Cycle: {sig_str}"
    )
    ax.text(
        0.02,
        0.96,
        hud_text,
        transform=ax.transAxes,
        fontsize=10,
        verticalalignment="top",
        bbox=dict(boxstyle="round,pad=0.6", facecolor="#0f172a", edgecolor="#38bdf8", alpha=0.9),
        color="#f8fafc",
        fontfamily="monospace",
    )

    leg = ax.legend(loc="upper right", facecolor="#0f172a", edgecolor="#334155", labelcolor="#f8fafc", fontsize=9)

    plt.tight_layout()
    output_image_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_image_path, dpi=200)
    plt.close()
    print(f"Generated road norms diagnostic visualization -> {output_image_path}")


def main():
    parser = argparse.ArgumentParser(description="NETRA M5: Self-Calibrating Road Norms Engine")
    parser.add_argument("--tracks", type=Path, default=Path("fixtures/tracks.sample.jsonl"), help="Path to input tracks.jsonl")
    parser.add_argument("--output", type=Path, default=Path("fixtures/norms.json"), help="Path to save output norms.json")
    parser.add_argument("--overlay", type=Path, default=Path("edge/norms/lane_overlay.png"), help="Path to save lane overlay visualization")
    args = parser.parse_args()

    if not args.tracks.exists():
        print(f"Error: Tracks file not found at {args.tracks}", file=sys.stderr)
        sys.exit(1)

    print(f"Ingesting tracks from {args.tracks}...")
    tracks = load_tracks_jsonl(args.tracks)
    print(f"Loaded {len(tracks)} frame detections.")

    norms_output = run_norms_pipeline(tracks)

    # Save norms.json
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(norms_output, f, indent=2)

    print(f"Successfully computed Road Norms (PRD §5.4):")
    print(f"  - 85th-Percentile Speed (V85): {norms_output['speed_85_kmh']} km/h")
    print(f"  - Track Sample Size: {norms_output['sample_size']}")
    print(f"  - Discovered Lanes: {len(norms_output['lanes'])}")
    for lane in norms_output["lanes"]:
        print(f"     Lane {lane['id']}: Heading {lane['heading_deg']}°, {len(lane['centreline_m'])} waypoints")
    print(f"  - Signal Cycle: {norms_output['signal_cycle_s']}")
    print(f"Saved norms to -> {args.output}")

    if args.overlay:
        render_lane_overlay(tracks, norms_output, args.overlay)


if __name__ == "__main__":
    main()
