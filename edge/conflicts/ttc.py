"""M3 — time-to-collision on the ground plane. Owner B.

Model each vehicle as a circle on the road surface, radius by class from
config, and assume constant velocity over the prediction horizon.

For vehicles *a* and *b*, with ``dp = p_a - p_b``, ``dv = v_a - v_b`` and
``R = r_a + r_b``, the circles touch when ``|dp + dv·t| = R``. Expanding gives
a quadratic in *t*::

    |dv|²·t²  +  2(dp·dv)·t  +  (|dp|² - R²)  =  0

so with ``A = |dv|²``, ``B = 2(dp·dv)``, ``C = |dp|² - R²``.

The circles are a simplification: vehicles are rectangles. Say so if asked.
Rectangle intersection is more accurate and considerably more code, and it is
not where 24 hours should go.

**All of this is in metres and seconds, on the ground plane, never in pixel
space.** That is the whole point of the project.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

#: |dv|² below this counts as no relative motion at all.
PARALLEL_EPSILON = 1e-6

# Case names, so callers can branch and report on them rather than guessing
# from the number.
CASE_PARALLEL = "parallel"
CASE_NEVER_WITHIN_R = "never_within_r"
CASE_DIVERGING = "diverging"
CASE_OVERLAPPING = "overlapping"
CASE_CLOSING = "closing"


@dataclass(frozen=True)
class TTCResult:
    """Outcome of one pairwise TTC computation.

    ``ttc_s`` is ``inf`` whenever the pair will not come within R. That is a
    normal, common answer — most pairs in any frame are not on a collision
    course — and callers must treat it as such rather than as an error.
    """

    ttc_s: float
    case: str
    suspicious: bool = False

    @property
    def is_finite(self) -> bool:
        return math.isfinite(self.ttc_s)


def time_to_collision(
    p_a, v_a, p_b, v_b, r_a: float, r_b: float
) -> TTCResult:
    """Solve the quadratic and classify the outcome.

    **The order of the checks below is load-bearing and easy to get wrong.**
    The PRD's case table lists "smallest root t < 0 -> diverging" before
    "C < 0 -> already overlapping". Implemented in that literal order it is a
    bug: when ``C < 0`` the discriminant is ``B² - 4AC > B² >= 0`` and the two
    roots always straddle zero, so the smallest root is *always* negative and
    every overlapping pair would be discarded as diverging. The overlap case
    must therefore be tested first.
    """
    dp = np.asarray(p_a, dtype=float) - np.asarray(p_b, dtype=float)
    dv = np.asarray(v_a, dtype=float) - np.asarray(v_b, dtype=float)
    R = float(r_a) + float(r_b)

    A = float(np.dot(dv, dv))
    B = 2.0 * float(np.dot(dp, dv))
    C = float(np.dot(dp, dp)) - R * R

    # Already overlapping. Checked first: see the docstring.
    if C < 0.0:
        # Reported as zero, but flagged rather than trusted. On real footage
        # this is usually two stopped vehicles queued at a signal, or a
        # projection error, not an imminent collision.
        return TTCResult(0.0, CASE_OVERLAPPING, suspicious=True)

    # No relative motion: the gap never changes.
    if A < PARALLEL_EPSILON:
        return TTCResult(math.inf, CASE_PARALLEL)

    disc = B * B - 4.0 * A * C
    if disc < 0.0:
        # The paths never bring the circles within R of one another.
        return TTCResult(math.inf, CASE_NEVER_WITHIN_R)

    t = (-B - math.sqrt(disc)) / (2.0 * A)
    if t < 0.0:
        # The encounter is behind us; the pair is separating.
        return TTCResult(math.inf, CASE_DIVERGING)

    return TTCResult(t, CASE_CLOSING)


def closing_speed_mps(p_a, v_a, p_b, v_b) -> float:
    """Rate at which the gap is shrinking, in m/s. Negative means opening.

    This is the projection of relative velocity onto the line joining the two
    vehicles, which is a much better guard against velocity noise than the raw
    magnitude of ``dv``: two vehicles running side by side at very different
    speeds have a large ``|dv|`` and a closing speed of zero.
    """
    dp = np.asarray(p_a, dtype=float) - np.asarray(p_b, dtype=float)
    dv = np.asarray(v_a, dtype=float) - np.asarray(v_b, dtype=float)
    dist = float(np.hypot(dp[0], dp[1]))
    if dist < 1e-9:
        return 0.0
    return -float(np.dot(dp, dv)) / dist


def severity_for(ttc_s: float, severe_s: float, conflict_s: float) -> str | None:
    """Classify a TTC. Returns None when it is not a conflict at all.

    Thresholds applied exactly, from config: below 0.8 s severe, below 1.5 s
    conflict.
    """
    if ttc_s < severe_s:
        return "severe"
    if ttc_s < conflict_s:
        return "conflict"
    return None
