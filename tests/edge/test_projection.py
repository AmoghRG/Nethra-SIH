"""M1: homography, bottom-centre projection, and velocity smoothing. Owner B."""

from __future__ import annotations

import math

import numpy as np
import pytest

from edge.calibration.homography import (
    CalibrationError,
    MAX_ACCEPTABLE_RMS_M,
    apply_homography,
    build_calibration_dict,
    fit_with_validation,
    solve_homography,
)
from edge.calibration.project import project_tracks, sliding_slope
from edge.common.config import load_config
from edge.common.jsonl import load_jsonl, read_jsonl

REFS4 = [
    {"pixel": [300, 690], "ground_m": [0.0, 0.0]},
    {"pixel": [980, 690], "ground_m": [24.0, 0.0]},
    {"pixel": [790, 400], "ground_m": [24.0, 40.0]},
    {"pixel": [490, 400], "ground_m": [0.0, 40.0]},
]


# -- the homography ---------------------------------------------------------


def test_four_points_reproject_exactly():
    H = solve_homography([r["pixel"] for r in REFS4], [r["ground_m"] for r in REFS4])
    got = apply_homography(H, [r["pixel"] for r in REFS4])
    truth = np.asarray([r["ground_m"] for r in REFS4])
    assert np.allclose(got, truth, atol=1e-6)


def test_perspective_is_not_linear_in_the_image():
    """Halfway down the image is NOT halfway across the ground.

    If this ever passes as a linear interpolation, the homography has collapsed
    to an affine transform and every far-field distance is wrong.
    """
    H = solve_homography([r["pixel"] for r in REFS4], [r["ground_m"] for r in REFS4])
    mid = apply_homography(H, [[640, 545]])[0]
    assert 10.0 < mid[1] < 15.0, "expected strong foreshortening, got {}".format(mid[1])


def test_four_points_alone_cannot_be_validated():
    """An exact fit has zero residual by construction. That means nothing."""
    _, rms, mode = fit_with_validation(REFS4)
    assert mode == "unvalidated"
    assert rms == 0.0


def test_held_out_point_produces_a_real_error_figure(fixtures_dir):
    from edge.calibration.homography import load_calibration

    calib = load_calibration(fixtures_dir / "calibration.json")
    assert 0.0 < calib.rms_error_m < MAX_ACCEPTABLE_RMS_M


def test_a_bad_calibration_is_refused_not_written():
    """Silently writing a wrong calibration is the failure this module exists to
    prevent, because everything downstream would still look fine."""
    bad = [dict(r) for r in REFS4] + [
        {"pixel": [640, 600], "ground_m": [12.0, 25.0], "held_out": True},  # ~18 m out
    ]
    with pytest.raises(CalibrationError, match="rms_error_m"):
        build_calibration_dict(
            video_id="bad", reference_points=bad, location=[13.0, 74.8],
            valid_region_px=[[0, 0], [1, 0], [1, 1]], max_range_m=45.0,
            method={"technique": "synthetic", "assumed_uncertainty_m": 0.1, "note": ""},
        )


# -- bottom-centre ----------------------------------------------------------


def test_ground_contact_uses_bottom_centre_not_the_centroid(calib):
    """The homography maps the road SURFACE; a centroid floats above it.

    This is the single most common calibration bug in traffic-vision projects,
    and it produces distances that look reasonable while being systematically
    wrong. A tall vehicle makes the discrepancy obvious.
    """
    bbox = [600.0, 500.0, 680.0, 620.0]   # 120 px tall: a bus, near-ish field
    contact = calib.ground_contact(bbox)
    assert contact is not None

    from edge.calibration.homography import project_point

    centroid = project_point(calib.H, ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0))
    bottom = project_point(calib.H, ((bbox[0] + bbox[2]) / 2.0, bbox[3]))
    assert contact == bottom
    assert contact != centroid
    # The error scales with vehicle height, and it is metres, not centimetres.
    assert math.dist(centroid, bottom) > 1.0


def test_detections_outside_the_validity_polygon_are_rejected(calib):
    """Amendment A2: near the vanishing point a one-pixel error is tens of metres."""
    assert calib.ground_contact([600.0, 380.0, 680.0, 395.0]) is None   # above the polygon
    assert calib.ground_contact([600.0, 560.0, 680.0, 620.0]) is not None


def test_projection_beyond_max_range_is_rejected(calib):
    calib.max_range_m = 5.0
    assert calib.ground_contact([600.0, 560.0, 680.0, 620.0]) is None


# -- velocity ---------------------------------------------------------------


def test_sliding_slope_recovers_a_known_constant_velocity():
    t = np.arange(0, 40) * 0.04
    x = 3.0 + 6.5 * t
    slope = sliding_slope(t, x, 7)
    assert np.allclose(slope, 6.5, atol=1e-9)


def test_sliding_slope_beats_raw_differencing_on_noisy_positions():
    """Detection jitter of two or three pixels becomes several km/h of phantom
    velocity, and phantom velocity is the leading cause of phantom conflicts."""
    rng = np.random.default_rng(7)
    t = np.arange(0, 60) * 0.04
    clean = 3.0 + 6.5 * t
    noisy = clean + rng.normal(0, 0.05, size=clean.shape)

    smoothed = sliding_slope(t, noisy, 7)
    differenced = np.diff(noisy) / np.diff(t)

    assert smoothed.std() < differenced.std() / 4.0
    assert abs(smoothed.mean() - 6.5) < 0.2


def test_sliding_slope_uses_timestamps_so_dropped_frames_are_safe():
    """A gap in a track must not read as a vehicle briefly teleporting."""
    t = np.array([0.0, 0.04, 0.08, 0.40, 0.44, 0.48, 0.52])
    x = 6.5 * t
    assert np.allclose(sliding_slope(t, x, 7), 6.5, atol=1e-9)


def test_an_overspeed_track_is_dropped_whole(cfg, calib, fixtures_dir):
    """A single implausible reading means the identity association broke, and
    the rest of that track cannot be trusted either.

    The perturbation is a STEP, not an alternation, because that is what an
    identity switch actually looks like: the id is handed to a different
    vehicle and stays there. An alternating jitter does not trip this guard at
    all, and should not - the least-squares window averages it away, which is
    precisely the job it is there to do.
    """
    rows = [dict(r) for r in read_jsonl(fixtures_dir / "tracks_px.sample.jsonl")]
    for row in rows:
        if row["track_id"] == 1 and row["frame"] >= 70:
            row["bbox"] = [row["bbox"][0] - 160, row["bbox"][1],
                           row["bbox"][2] - 160, row["bbox"][3]]

    out, stats = project_tracks(rows, calib, cfg)
    assert stats.tracks_dropped_overspeed >= 1
    assert 1 in stats.overspeed_track_ids
    assert stats.tracks_dropped_short == 0, "must be dropped for speed, not for length"
    assert all(r["track_id"] != 1 for r in out), "the whole track must go, not just the bad rows"


def test_alternating_jitter_does_not_trip_the_overspeed_guard(cfg, calib, fixtures_dir):
    """The counterpart to the test above, and the reason smoothing exists.

    Frame-to-frame jitter must be absorbed, not mistaken for a 500 km/h
    vehicle. If this ever starts failing, the velocity fit has been replaced by
    something closer to raw differencing.
    """
    rows = [dict(r) for r in read_jsonl(fixtures_dir / "tracks_px.sample.jsonl")]
    for row in rows:
        if row["track_id"] == 1 and row["frame"] % 2 == 0:
            row["bbox"] = [row["bbox"][0] - 60, row["bbox"][1],
                           row["bbox"][2] - 60, row["bbox"][3]]

    _, stats = project_tracks(rows, calib, cfg)
    assert stats.tracks_dropped_overspeed == 0


def test_no_projected_speed_exceeds_the_configured_maximum(fixtures_dir):
    rows = load_jsonl(fixtures_dir / "tracks_m.sample.jsonl")
    assert rows
    assert max(r["speed_kmh"] for r in rows) <= 150.0


def test_projected_speed_is_smooth_not_sawtooth(fixtures_dir):
    """Acceptance criterion: a vehicle crossing the frame produces a smoothly
    varying speed. Verified here as a bound on frame-to-frame change."""
    rows = [r for r in load_jsonl(fixtures_dir / "tracks_m.sample.jsonl") if r["track_id"] == 1]
    rows.sort(key=lambda r: r["frame"])
    speeds = [r["speed_kmh"] for r in rows]
    jumps = [abs(b - a) for a, b in zip(speeds, speeds[1:])]
    assert max(jumps) < 8.0, "speed is jumping frame to frame; smoothing is not working"


def test_projection_output_matches_the_contract(fixtures_dir):
    required = {"frame", "t", "track_id", "cls", "conf", "ground_m", "v_mps",
                "speed_kmh", "heading_deg"}
    for row in load_jsonl(fixtures_dir / "tracks_m.sample.jsonl"):
        assert set(row) == required
        assert 0.0 <= row["heading_deg"] < 360.0
