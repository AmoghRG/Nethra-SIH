"""M1 — the projection stage. Owner B.

Reads ``tracks_px.jsonl`` (owner A's output, pixels only) and writes
``tracks_m.jsonl`` (ground metres, velocity, speed, heading). This is the hard
seam created by amendment A1: A and B share no source file, and B can build
the entire conflict engine against a fixture track file before A's detector
has produced a single frame.

Two things in here are load-bearing.

**Bottom-centre projection.** See :meth:`Calibration.ground_contact`. The
homography maps the road surface; a centroid floats above it.

**Least-squares velocity.** Frame-to-frame differencing of positions is far
too noisy to use directly. Detection jitter of two or three pixels becomes
several km/h of phantom velocity, and phantom velocity is the leading cause of
phantom conflicts. Velocity here is the slope of a straight line fitted
through a sliding window of positions. It is a few lines and it is not
optional.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from edge.calibration.homography import Calibration, load_calibration
from edge.common.config import Config, add_config_args, config_from_args
from edge.common.geometry import heading_deg, kmh_to_mps, mps_to_kmh, speed_mps
from edge.common.jsonl import read_jsonl, write_jsonl


@dataclass
class ProjectionStats:
    """Counts worth printing, because each one is a distinct failure mode."""

    rows_in: int = 0
    rows_out: int = 0
    rejected_outside_region: int = 0
    tracks_in: int = 0
    tracks_dropped_short: int = 0
    tracks_dropped_overspeed: int = 0
    overspeed_track_ids: list[int] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "projection:",
            "  rows in                     {}".format(self.rows_in),
            "  rows out                    {}".format(self.rows_out),
            "  rejected outside validity   {}  (amendment A2: far-field geometry)".format(
                self.rejected_outside_region
            ),
            "  tracks in                   {}".format(self.tracks_in),
            "  tracks dropped, too short   {}".format(self.tracks_dropped_short),
            "  tracks dropped, overspeed   {}  {}".format(
                self.tracks_dropped_overspeed,
                self.overspeed_track_ids[:10] if self.overspeed_track_ids else "",
            ),
        ]
        if self.tracks_dropped_overspeed:
            lines.append(
                "  note: an overspeed track on urban footage means an identity "
                "switch, not a fast vehicle."
            )
        return "\n".join(lines)


def sliding_slope(t: np.ndarray, v: np.ndarray, window: int) -> np.ndarray:
    """Slope of a least-squares line through a sliding window, per sample.

    Uses real timestamps rather than frame indices, so a track with dropped
    frames is handled correctly instead of reporting a vehicle that briefly
    teleported.

    Near the ends of a track a full centred window does not exist. Rather than
    fit a two-point line there (which is just differencing again, with all its
    noise), the window is clamped to the nearest full one. The first and last
    few samples therefore carry the velocity of the segment beside them, which
    is a far better estimate than the noisy alternative.
    """
    n = len(t)
    if n < 2:
        return np.zeros(n, dtype=float)

    w = min(int(window), n)
    if w < 2:
        w = 2

    # Every full window, vectorised: slope = Σ(t-t̄)(v-v̄) / Σ(t-t̄)²
    starts = np.arange(0, n - w + 1)
    idx = starts[:, None] + np.arange(w)[None, :]
    tw = t[idx]
    vw = v[idx]
    tc = tw - tw.mean(axis=1, keepdims=True)
    vc = vw - vw.mean(axis=1, keepdims=True)
    denom = np.sum(tc * tc, axis=1)
    slopes = np.divide(
        np.sum(tc * vc, axis=1), denom, out=np.zeros_like(denom), where=denom > 1e-12
    )

    # Map each sample to the window centred on it, clamped at the ends.
    half = w // 2
    centre_of = np.clip(np.arange(n) - half, 0, len(starts) - 1)
    return slopes[centre_of]


def project_tracks(
    rows: Iterable[dict[str, Any]], calib: Calibration, cfg: Config
) -> tuple[list[dict[str, Any]], ProjectionStats]:
    """Project pixel tracks to the ground plane and estimate velocity."""
    stats = ProjectionStats()
    use_region = bool(cfg.get("suppression.validity_region", True))
    window = int(cfg.get("geometry.smooth_window"))
    max_speed_mps = kmh_to_mps(float(cfg.get("geometry.max_speed_kmh")))
    min_frames = int(cfg.get("tracker.min_track_frames"))

    by_track: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        stats.rows_in += 1
        if use_region:
            ground = calib.ground_contact(row["bbox"])
        else:
            x1, y1, x2, y2 = (float(v) for v in row["bbox"])
            from edge.calibration.homography import project_point

            ground = project_point(calib.H, ((x1 + x2) / 2.0, y2))
        if ground is None:
            stats.rejected_outside_region += 1
            continue
        by_track[int(row["track_id"])].append({**row, "_ground": ground})

    stats.tracks_in = len(by_track)
    out: list[dict[str, Any]] = []

    for track_id, samples in by_track.items():
        samples.sort(key=lambda r: r["frame"])
        if len(samples) < min_frames:
            stats.tracks_dropped_short += 1
            continue

        t = np.asarray([float(s["t"]) for s in samples])
        gx = np.asarray([s["_ground"][0] for s in samples])
        gy = np.asarray([s["_ground"][1] for s in samples])

        vx = sliding_slope(t, gx, window)
        vy = sliding_slope(t, gy, window)
        speeds = np.hypot(vx, vy)

        # Drop the WHOLE track, not just the offending sample. A single
        # implausible reading means the identity association broke somewhere,
        # and the rest of that track cannot be trusted either.
        if float(speeds.max()) > max_speed_mps:
            stats.tracks_dropped_overspeed += 1
            stats.overspeed_track_ids.append(track_id)
            continue

        for i, s in enumerate(samples):
            v = (float(vx[i]), float(vy[i]))
            out.append(
                {
                    "frame": int(s["frame"]),
                    "t": round(float(s["t"]), 3),
                    "track_id": track_id,
                    "cls": s["cls"],
                    "conf": round(float(s["conf"]), 3),
                    "ground_m": [round(float(gx[i]), 3), round(float(gy[i]), 3)],
                    "v_mps": [round(v[0], 3), round(v[1], 3)],
                    "speed_kmh": round(mps_to_kmh(speed_mps(v)), 2),
                    # Re-wrapped after rounding: 359.97 rounds to 360.0, which
                    # is outside the contract's [0, 360) range.
                    "heading_deg": round(heading_deg(v), 1) % 360.0,
                }
            )

    out.sort(key=lambda r: (r["frame"], r["track_id"]))
    stats.rows_out = len(out)
    return out, stats


def draw_projection_check(
    frame_path: str | Path, calib: Calibration, rows: list[dict[str, Any]], out_path: str | Path
) -> None:
    """Overlay projected ground positions back onto a frame.

    This is the acceptance check from edge PRD 4.6 made into one command:
    the markers must sit at the tyres, not the roofline. Needs OpenCV, so it
    is imported here and not at module scope.
    """
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "the projection overlay needs OpenCV: pip install opencv-python"
        ) from exc

    img = cv2.imread(str(frame_path))
    if img is None:
        raise RuntimeError("could not read frame: {}".format(frame_path))

    poly = np.asarray(calib.valid_region_px, dtype=np.int32).reshape(-1, 1, 2)
    cv2.polylines(img, [poly], True, (0, 200, 255), 2)

    for row in rows:
        # We only have ground coords here, so go back through H inverse.
        H_inv = np.linalg.inv(calib.H)
        g = np.array([row["ground_m"][0], row["ground_m"][1], 1.0])
        p = H_inv @ g
        if abs(p[2]) < 1e-9:
            continue
        px, py = int(p[0] / p[2]), int(p[1] / p[2])
        cv2.drawMarker(img, (px, py), (0, 0, 255), cv2.MARKER_CROSS, 12, 2)
        cv2.putText(
            img,
            "{:.0f}m".format(float(np.hypot(*row["ground_m"]))),
            (px + 6, py - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (0, 0, 255),
            1,
        )

    cv2.imwrite(str(out_path), img)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M1 projection: tracks_px.jsonl -> tracks_m.jsonl (owner B)"
    )
    parser.add_argument("--tracks", required=True, help="input tracks_px.jsonl")
    parser.add_argument("--calib", required=True, help="calibration.json")
    parser.add_argument("--out", required=True, help="output tracks_m.jsonl")
    parser.add_argument(
        "--verify-frame",
        default=None,
        help="draw projected positions onto this frame (needs OpenCV)",
    )
    parser.add_argument("--verify-out", default="out/projection_check.jpg")
    add_config_args(parser)
    args = parser.parse_args(argv)

    cfg = config_from_args(args)
    calib = load_calibration(args.calib)
    rows, stats = project_tracks(read_jsonl(args.tracks), calib, cfg)

    write_jsonl(
        args.out,
        rows,
        header=(
            "tracks_m.jsonl - ground plane, metres. Produced by edge/calibration/project.py\n"
            "calibration {} rms_error_m {}".format(calib.video_id, calib.rms_error_m)
        ),
    )
    print(stats.render())
    print("wrote {} rows to {}".format(len(rows), args.out))

    if args.verify_frame:
        draw_projection_check(args.verify_frame, calib, rows[:400], args.verify_out)
        print("projection check written to {}".format(args.verify_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
