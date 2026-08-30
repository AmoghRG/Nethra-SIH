"""Ground-plane geometry helpers.

Jointly owned by A and B.

Ground coordinate frame, fixed for the whole sprint: origin at the first
calibration reference point, X east, Y north, both in metres, right-handed.

Units: everything here is SI. Conversion to km/h happens only at JSON
serialisation, through ``mps_to_kmh``. Mixed units are the most common silent
bug in this kind of pipeline, and they produce plausible-looking wrong answers
rather than crashes.
"""

from __future__ import annotations

import math

import numpy as np

MPS_TO_KMH = 3.6


def mps_to_kmh(v: float) -> float:
    return v * MPS_TO_KMH


def kmh_to_mps(v: float) -> float:
    return v / MPS_TO_KMH


def speed_mps(v) -> float:
    return float(math.hypot(float(v[0]), float(v[1])))


def heading_deg(v) -> float:
    """Compass-style bearing of a velocity vector.

    Zero degrees is +Y (north), increasing clockwise, range [0, 360). Returns
    0.0 for a stationary vehicle, which is meaningless but harmless: every
    caller that cares about heading also gates on speed.
    """
    vx, vy = float(v[0]), float(v[1])
    if abs(vx) < 1e-12 and abs(vy) < 1e-12:
        return 0.0
    return math.degrees(math.atan2(vx, vy)) % 360.0


def heading_difference_deg(a: float, b: float) -> float:
    """Smallest absolute angle between two bearings, in [0, 180]."""
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def point_in_polygon(point, polygon) -> bool:
    """Ray-casting test, used for the A2 validity region.

    Points lying exactly on an edge are not guaranteed either way. At the
    scale of a validity polygon that ambiguity costs less than the code to
    resolve it would.
    """
    x, y = float(point[0]), float(point[1])
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = float(polygon[i][0]), float(polygon[i][1])
        x2, y2 = float(polygon[(i + 1) % n][0]), float(polygon[(i + 1) % n][1])
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


def decompose_separation(dp, reference_heading_deg: float) -> tuple[float, float]:
    """Split a separation vector into (longitudinal, lateral) components.

    Longitudinal runs along the reference heading, lateral perpendicular to
    it.

    This is what distinguishes a motorcycle filtering ALONGSIDE a car (small
    longitudinal gap, steady lateral gap) from a car following BEHIND another
    (large longitudinal gap, near-zero lateral gap). Suppression rule 2
    depends on the distinction: collapsing the two would throw away every
    genuine rear-end conflict along with the lane-splitting noise.
    """
    theta = math.radians(reference_heading_deg)
    fwd = np.array([math.sin(theta), math.cos(theta)])
    left = np.array([-fwd[1], fwd[0]])
    dp = np.asarray(dp, dtype=float)
    return float(np.dot(dp, fwd)), float(np.dot(dp, left))
