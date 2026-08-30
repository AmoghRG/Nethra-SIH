"""M1 — solving and validating the pixel-to-metre homography. Owner B.

A camera produces a flat image with no depth. Two vehicles that look equally
far apart on screen may be 2 metres or 20 metres apart in reality, depending
entirely on where they sit in the frame. Every downstream number (speed,
distance, time-to-collision) is meaningless until this is fixed.

The road is approximately flat, so one 3x3 matrix describes the whole surface.

**Why this is numpy and not cv2.** The solve is a direct linear transform:
about forty lines with ``numpy.linalg.svd``. Keeping it dependency-free means
owner B's entire critical path (calibrate, project, TTC, suppress, emit) runs
before anyone has installed ultralytics or OpenCV. cv2 is needed only to
*look* at images, which is ``extract_frame.py`` and ``pick_points.py``.

**The error budget, which you should know before you are asked.**
Calibration scale error propagates linearly and unforgivingly:

- 10% scale error -> 10% speed error -> roughly 10% TTC error
- a conflict at a true TTC of 0.85 s is reported at 0.77 s, and so is
  misclassified as *severe*

A 10% calibration error therefore moves events across the severity threshold.
It does not merely add noise, it changes the headline counts. Hence the
held-out validation below, and hence :data:`MAX_ACCEPTABLE_RMS_M` being a
refusal rather than a warning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from edge.common.geometry import point_in_polygon

#: Above this, re-pick the points before building anything on top. A wrong
#: calibration is worse than no calibration, because it looks fine.
MAX_ACCEPTABLE_RMS_M = 0.5


class CalibrationError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Solving
# ---------------------------------------------------------------------------


def _normalise(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Hartley normalisation: centre on the origin, scale to mean distance √2.

    Pixel coordinates run to ~1200 while ground coordinates run to ~40. Feeding
    that spread straight into an SVD is badly conditioned and quietly loses
    precision in the matrix, which then shows up as a plausible-looking
    calibration that is subtly wrong. Fifteen lines to avoid that is a good
    trade.
    """
    centroid = points.mean(axis=0)
    centred = points - centroid
    mean_dist = float(np.mean(np.hypot(centred[:, 0], centred[:, 1])))
    scale = np.sqrt(2.0) / mean_dist if mean_dist > 1e-12 else 1.0
    T = np.array(
        [
            [scale, 0.0, -scale * centroid[0]],
            [0.0, scale, -scale * centroid[1]],
            [0.0, 0.0, 1.0],
        ]
    )
    return centred * scale, T


def solve_homography(src: Sequence[Sequence[float]], dst: Sequence[Sequence[float]]) -> np.ndarray:
    """Solve H mapping ``src`` (pixels) to ``dst`` (ground metres).

    Four points give an exact solution; more than four are fitted in the
    least-squares sense, which is what the SVD gives for free. Points must be
    coplanar (they are: they are all on the road surface) and no three may be
    collinear.
    """
    src_arr = np.asarray(src, dtype=float)
    dst_arr = np.asarray(dst, dtype=float)
    if src_arr.shape != dst_arr.shape or src_arr.shape[0] < 4:
        raise CalibrationError(
            "need at least 4 matched point pairs, got {} src and {} dst".format(
                src_arr.shape[0], dst_arr.shape[0]
            )
        )

    src_n, T_src = _normalise(src_arr)
    dst_n, T_dst = _normalise(dst_arr)

    rows = []
    for (x, y), (X, Y) in zip(src_n, dst_n):
        rows.append([-x, -y, -1.0, 0.0, 0.0, 0.0, X * x, X * y, X])
        rows.append([0.0, 0.0, 0.0, -x, -y, -1.0, Y * x, Y * y, Y])
    A = np.asarray(rows, dtype=float)

    _, _, vt = np.linalg.svd(A)
    H_n = vt[-1].reshape(3, 3)

    # Undo the normalisation: H = T_dst^-1 · H_n · T_src
    H = np.linalg.inv(T_dst) @ H_n @ T_src
    if abs(H[2, 2]) < 1e-12:
        raise CalibrationError("degenerate homography; check for collinear points")
    return H / H[2, 2]


def apply_homography(H: np.ndarray, points: Sequence[Sequence[float]]) -> np.ndarray:
    """Map pixel points to ground metres. Accepts one point or many."""
    pts = np.atleast_2d(np.asarray(points, dtype=float))
    homogeneous = np.hstack([pts, np.ones((pts.shape[0], 1))])
    projected = homogeneous @ np.asarray(H, dtype=float).T
    w = projected[:, 2:3]
    # A point on the horizon projects to infinity. Guard rather than emit inf,
    # which would silently poison every distance computed from it.
    w = np.where(np.abs(w) < 1e-12, np.nan, w)
    return projected[:, :2] / w


def project_point(H: np.ndarray, point: Sequence[float]) -> tuple[float, float] | None:
    """Project a single pixel point, returning None if it is degenerate."""
    out = apply_homography(H, [point])[0]
    if not np.all(np.isfinite(out)):
        return None
    return float(out[0]), float(out[1])


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def residuals_m(H: np.ndarray, reference_points: Sequence[dict[str, Any]]) -> np.ndarray:
    """Per-point re-projection error in metres."""
    src = [p["pixel"] for p in reference_points]
    truth = np.asarray([p["ground_m"] for p in reference_points], dtype=float)
    got = apply_homography(H, src)
    return np.hypot(got[:, 0] - truth[:, 0], got[:, 1] - truth[:, 1])


def fit_with_validation(
    reference_points: Sequence[dict[str, Any]]
) -> tuple[np.ndarray, float, str]:
    """Fit H and measure it on data the fit did not see.

    Returns ``(H, rms_error_m, mode)`` where mode explains how the number was
    obtained, because "0.0 m error" from an exact four-point fit means nothing
    at all and must never be reported as if it did.

    - ``holdout``: points flagged ``held_out`` were excluded from the fit and
      re-projected. This is the method the PRD asks for.
    - ``loo``: no point was flagged, but there are at least five, so every
      point is left out in turn and the residuals pooled. Strictly better
      evidence than a single holdout, and free.
    - ``unvalidated``: exactly four points and none held out. The fit is exact
      by construction and the error is unmeasured. Reported as such.
    """
    pts = list(reference_points)
    held = [p for p in pts if p.get("held_out")]
    fit_pts = [p for p in pts if not p.get("held_out")]

    if held:
        if len(fit_pts) < 4:
            raise CalibrationError(
                "need 4 points to fit after holding out {}; have {}".format(
                    len(held), len(fit_pts)
                )
            )
        H = solve_homography([p["pixel"] for p in fit_pts], [p["ground_m"] for p in fit_pts])
        rms = float(np.sqrt(np.mean(residuals_m(H, held) ** 2)))
        return H, rms, "holdout"

    H = solve_homography([p["pixel"] for p in pts], [p["ground_m"] for p in pts])

    if len(pts) >= 5:
        errs = []
        for i in range(len(pts)):
            subset = pts[:i] + pts[i + 1 :]
            H_i = solve_homography(
                [p["pixel"] for p in subset], [p["ground_m"] for p in subset]
            )
            errs.append(float(residuals_m(H_i, [pts[i]])[0]))
        return H, float(np.sqrt(np.mean(np.square(errs)))), "loo"

    return H, 0.0, "unvalidated"


# ---------------------------------------------------------------------------
# calibration.json
# ---------------------------------------------------------------------------


@dataclass
class Calibration:
    """A loaded calibration, ready to project with."""

    video_id: str
    H: np.ndarray
    location: list[float]
    valid_region_px: list[list[float]]
    max_range_m: float
    rms_error_m: float

    def ground_contact(self, bbox: Sequence[float]) -> tuple[float, float] | None:
        """Project a bounding box to the ground, or None if it is not trusted.

        **Bottom-centre, not the centroid.** This matters more than it looks.
        The homography maps the road SURFACE. A vehicle's centroid floats above
        that surface, so projecting it places a bus several metres from where
        it actually is, and the error scales with vehicle height. The
        bottom-centre of the box is where the tyres meet the road, and it is
        the only correct choice.

        This is the single most common calibration bug in traffic-vision
        projects, and it produces distances that look reasonable while being
        systematically wrong.
        """
        x1, y1, x2, y2 = (float(v) for v in bbox)
        contact_px = ((x1 + x2) / 2.0, y2)

        if self.valid_region_px and not point_in_polygon(contact_px, self.valid_region_px):
            return None

        ground = project_point(self.H, contact_px)
        if ground is None:
            return None
        if float(np.hypot(ground[0], ground[1])) > self.max_range_m:
            return None
        return ground


def build_calibration_dict(
    *,
    video_id: str,
    reference_points: Sequence[dict[str, Any]],
    location: Sequence[float],
    valid_region_px: Sequence[Sequence[float]],
    max_range_m: float,
    method: dict[str, Any],
    image_size_px: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Fit, validate, and assemble a calibration.json payload.

    Raises if the measured error is above :data:`MAX_ACCEPTABLE_RMS_M`. That is
    deliberate: silently writing a bad calibration is the failure mode this
    whole module exists to prevent, and everything downstream would look fine.
    """
    H, rms, mode = fit_with_validation(reference_points)
    if mode != "unvalidated" and rms > MAX_ACCEPTABLE_RMS_M:
        raise CalibrationError(
            "rms_error_m {:.3f} exceeds {:.2f} m ({} validation). Re-pick the "
            "points: spread them wider, prefer the near and middle field, and "
            "keep away from the horizon.".format(rms, MAX_ACCEPTABLE_RMS_M, mode)
        )

    payload: dict[str, Any] = {
        "video_id": video_id,
        "homography": [[float(v) for v in row] for row in H],
        "reference_points": [dict(p) for p in reference_points],
        "rms_error_m": round(float(rms), 4),
        "location": [float(location[0]), float(location[1])],
        "valid_region_px": [[float(p[0]), float(p[1])] for p in valid_region_px],
        "max_range_m": float(max_range_m),
        "method": dict(method),
    }
    if image_size_px is not None:
        payload["image_size_px"] = [int(image_size_px[0]), int(image_size_px[1])]
    payload["method"].setdefault("validation", mode)
    return payload


def load_calibration(path: str | Path) -> Calibration:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    missing = [
        k
        for k in ("video_id", "homography", "location", "valid_region_px", "max_range_m")
        if k not in data
    ]
    if missing:
        raise CalibrationError(
            "{}: calibration is missing required fields: {}".format(path, missing)
        )
    return Calibration(
        video_id=data["video_id"],
        H=np.asarray(data["homography"], dtype=float),
        location=list(data["location"]),
        valid_region_px=[list(p) for p in data["valid_region_px"]],
        max_range_m=float(data["max_range_m"]),
        rms_error_m=float(data.get("rms_error_m", 0.0)),
    )
