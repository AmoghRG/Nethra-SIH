"""The quadratic, and all five cases of its truth table. Owner B.

``synthetic_collision.jsonl`` carries its analytically computed answer in its
own header. If a number here moves, the maths in ``ttc.py`` is wrong; nothing
about the road changed.
"""

from __future__ import annotations

import math

import pytest

from edge.common.jsonl import load_jsonl
from edge.conflicts.ttc import (
    CASE_CLOSING,
    CASE_DIVERGING,
    CASE_NEVER_WITHIN_R,
    CASE_OVERLAPPING,
    CASE_PARALLEL,
    closing_speed_mps,
    severity_for,
    time_to_collision,
)


def test_synthetic_fixture_first_frame_is_analytically_exact(fixtures_dir):
    """Two cars head-on 40 m apart at 10 m/s each: TTC is exactly 1.8 s.

    A = 400, B = -1600, C = 1584, disc = 25600, sqrt = 160,
    t = (1600 - 160) / 800 = 1.8
    """
    rows = load_jsonl(fixtures_dir / "synthetic_collision.jsonl")
    a, b = [r for r in rows if r["frame"] == 0]
    result = time_to_collision(a["ground_m"], a["v_mps"], b["ground_m"], b["v_mps"], 2.0, 2.0)
    assert result.case == CASE_CLOSING
    assert result.ttc_s == 1.8


def test_synthetic_fixture_last_frame_is_analytically_exact(fixtures_dir):
    """At frame 27 the gap is 18.4 m and TTC is 0.72 s.

    Compared with a tolerance rather than for equality: unlike frame 0, this
    one lands on a value binary floating point cannot represent exactly. The
    tolerance is far tighter than any error that would signal a real fault.
    """
    rows = load_jsonl(fixtures_dir / "synthetic_collision.jsonl")
    a, b = [r for r in rows if r["frame"] == 27]
    result = time_to_collision(a["ground_m"], a["v_mps"], b["ground_m"], b["v_mps"], 2.0, 2.0)
    assert result.ttc_s == pytest.approx(0.72, abs=1e-9)


def test_case_closing():
    result = time_to_collision([0, 0], [10, 0], [20, 0], [-10, 0], 2.0, 2.0)
    assert result.case == CASE_CLOSING
    assert result.ttc_s == 0.8
    assert result.is_finite


def test_case_parallel_no_relative_motion():
    result = time_to_collision([0, 0], [10, 0], [0, 5], [10, 0], 2.0, 2.0)
    assert result.case == CASE_PARALLEL
    assert math.isinf(result.ttc_s)


def test_case_never_within_r():
    """Paths that cross but never bring the circles within R."""
    result = time_to_collision([0, 0], [10, 0], [0, 20], [-10, 0], 2.0, 2.0)
    assert result.case == CASE_NEVER_WITHIN_R
    assert math.isinf(result.ttc_s)


def test_case_diverging_encounter_already_past():
    result = time_to_collision([0, 0], [10, 0], [-40, 0], [-10, 0], 2.0, 2.0)
    assert result.case == CASE_DIVERGING
    assert math.isinf(result.ttc_s)


def test_overlapping_is_checked_before_the_root_sign():
    """The ordering trap, guarded explicitly.

    The PRD's case table lists "smallest root < 0 -> diverging" ahead of
    "C < 0 -> overlapping". Implemented in that literal order it is a bug: when
    C < 0 the discriminant exceeds B squared, so the roots always straddle zero
    and the smallest is always negative. Every overlapping pair would then be
    discarded as diverging.
    """
    result = time_to_collision([0, 0], [0.1, 0], [2, 0], [0, 0], 2.0, 2.0)
    assert result.case == CASE_OVERLAPPING
    assert result.ttc_s == 0.0
    assert result.suspicious, "an overlap must be flagged, not trusted as a real TTC of zero"


def test_closing_speed_uses_the_line_joining_the_vehicles():
    """Raw |dv| is the wrong guard; two vehicles abreast are not approaching."""
    assert closing_speed_mps([0, 0], [10, 0], [40, 0], [-10, 0]) == 20.0
    abreast = closing_speed_mps([0, 0], [10, 0], [0, 3], [4, 0])
    assert abs(abreast) < 1e-9


def test_severity_thresholds_are_applied_exactly():
    assert severity_for(0.79, 0.8, 1.5) == "severe"
    assert severity_for(0.8, 0.8, 1.5) == "conflict"
    assert severity_for(1.49, 0.8, 1.5) == "conflict"
    assert severity_for(1.5, 0.8, 1.5) is None
