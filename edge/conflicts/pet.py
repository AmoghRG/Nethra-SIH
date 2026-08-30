"""M3 — post-encroachment time. Owner B. **This is the first thing to cut.**

Where TTC is predictive ("if both continue, when do they touch?"), PET is
retrospective: find where two ground-plane paths cross, then measure the gap
between the first vehicle clearing that point and the second arriving.

    PET = t_arrival(B) - t_departure(A)

PET catches encounters TTC misses, particularly two vehicles that were never
on a true collision course but passed through the same space uncomfortably
close in time.

**Cut line: PET goes first.** TTC alone is a complete and defensible story, and
every event's ``pet_s`` is nullable precisely so this module can disappear
without breaking the contract.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from edge.conflicts.sample import TrackSample


@dataclass(frozen=True)
class PETResult:
    pet_s: float
    crossing_m: tuple[float, float]
    t_first: float
    t_second: float


def _segment_intersection(
    p1: np.ndarray, p2: np.ndarray, q1: np.ndarray, q2: np.ndarray
) -> tuple[float, float] | None:
    """Return ``(u, v)`` parameters where two segments cross, or None.

    ``u`` and ``v`` are the fractions along each segment, both in [0, 1], so
    the caller can interpolate the time at which each vehicle was there.
    """
    r = p2 - p1
    s = q2 - q1
    denom = r[0] * s[1] - r[1] * s[0]
    if abs(denom) < 1e-12:
        return None  # parallel or degenerate
    diff = q1 - p1
    u = (diff[0] * s[1] - diff[1] * s[0]) / denom
    v = (diff[0] * r[1] - diff[1] * r[0]) / denom
    if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
        return float(u), float(v)
    return None


def post_encroachment_time(
    track_a: list[TrackSample],
    track_b: list[TrackSample],
    around_t: float | None = None,
    max_offset_s: float = 5.0,
) -> PETResult | None:
    """Smallest PET over every place the two paths cross. None if they never do.

    Two vehicles travelling the same road in the same direction never cross,
    so None is the common and correct answer for following traffic. It is not
    an error, and it is why ``pet_s`` is nullable in the contract.

    ``around_t`` restricts the search to crossings near a given moment, which
    is what makes the number describe *this* encounter. A pair of long tracks
    can cross paths more than once over a clip; without the restriction the
    smallest PET anywhere in the file gets attached to an encounter it has
    nothing to do with, and the value looks plausible while being unrelated.
    """
    if len(track_a) < 2 or len(track_b) < 2:
        return None

    best: PETResult | None = None
    for i in range(len(track_a) - 1):
        a1, a2 = track_a[i], track_a[i + 1]
        for j in range(len(track_b) - 1):
            b1, b2 = track_b[j], track_b[j + 1]
            hit = _segment_intersection(a1.p, a2.p, b1.p, b2.p)
            if hit is None:
                continue
            u, v = hit
            t_a = a1.t + u * (a2.t - a1.t)
            t_b = b1.t + v * (b2.t - b1.t)
            if around_t is not None and min(
                abs(t_a - around_t), abs(t_b - around_t)
            ) > max_offset_s:
                continue
            pet = abs(t_b - t_a)
            if best is None or pet < best.pet_s:
                point = a1.p + u * (a2.p - a1.p)
                best = PETResult(
                    pet_s=pet,
                    crossing_m=(float(point[0]), float(point[1])),
                    t_first=min(t_a, t_b),
                    t_second=max(t_a, t_b),
                )
    return best
