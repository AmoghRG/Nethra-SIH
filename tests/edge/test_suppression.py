"""The six suppression rules. Owner B.

The lane-splitting test is the important one in this file. If it starts
failing, the conflict stream on real Indian footage is about to be flooded with
two-wheelers doing nothing wrong, and recall against C's human labels will
collapse - because the labeller marked none of it.
"""

from __future__ import annotations

from edge.common.config import load_config
from edge.common.jsonl import load_jsonl
from edge.conflicts.engine import ConflictEngine
from edge.conflicts.sample import TrackSample
from edge.conflicts.suppression import (
    RULE_LANE_SPLITTING,
    RULE_SPEED_SANITY,
    RULE_STOPPED,
    RULE_VELOCITY_NOISE,
    SuppressionEngine,
)


def _sample(track_id, cls, p, v, frame=0, t=0.0, conf=0.9):
    from edge.common.geometry import heading_deg

    return TrackSample.from_row({
        "frame": frame, "t": t, "track_id": track_id, "cls": cls, "conf": conf,
        "ground_m": list(p), "v_mps": list(v),
        "speed_kmh": 0.0, "heading_deg": heading_deg(v),
    })


# -- rule 2, the one that matters -------------------------------------------


def test_lane_split_fixture_produces_zero_events(fixtures_dir, calib):
    """A motorcycle filtering between two cars is normal traffic, not a near-miss."""
    rows = load_jsonl(fixtures_dir / "lane_split.jsonl")
    events = ConflictEngine(load_config(), calib).run(rows)
    assert events == [], "lane-splitting must be suppressed entirely"


def test_lane_split_fixture_floods_when_rule_2_is_off(fixtures_dir, calib):
    """The contrast is the demo beat, so it is worth a test of its own."""
    cfg = load_config(overrides=["suppression.lane_splitting=false"])
    engine = ConflictEngine(cfg, calib)
    events = engine.run(load_jsonl(fixtures_dir / "lane_split.jsonl"))
    assert events, "with rule 2 off this fixture must produce false conflicts"
    assert all(e.severity == "severe" for e in events)


def test_rule_2_preserves_a_genuine_rear_end_conflict(cfg):
    """Same path, gap ~0: a follower is not a filterer and must not be suppressed.

    This is the discriminator the rule turns on. An earlier version tested
    longitudinal distance instead and got this backwards in both directions.
    """
    sup = SuppressionEngine(cfg, max_range_m=100.0)
    # Behind, same lane, closing fast.
    for frame in range(10):
        t = frame * 0.04
        lead = _sample(1, "car", (10.0, 40.0 + 5.0 * t), (0.0, 5.0), frame, t)
        follow = _sample(2, "car", (10.0, 30.0 + 12.0 * t), (0.0, 12.0), frame, t)
        sup.observe(lead, follow)
    assert not sup._lane_splitting(lead, follow)


def test_check_returns_the_first_rule_that_fires(cfg):
    """check() is ordered, so a rule-2 assertion on it can pass for the wrong
    reason. Rule-2 behaviour is asserted against _lane_splitting directly."""
    sup = SuppressionEngine(cfg, max_range_m=100.0)
    a = _sample(1, "car", (9.0, 20.0), (0.0, 5.0))
    b = _sample(2, "motorcycle", (10.5, 20.0), (0.0, 8.0))
    sup.observe(a, b)
    assert sup.check(a, b) == RULE_VELOCITY_NOISE


# -- the other rules --------------------------------------------------------


def test_rule_3_suppresses_queued_traffic(cfg):
    """Stopped vehicles sit within a couple of metres and yield a meaningless TTC of 0."""
    sup = SuppressionEngine(cfg, max_range_m=100.0)
    a = _sample(1, "car", (10.0, 20.0), (0.0, 0.2))
    b = _sample(2, "car", (10.0, 24.0), (0.0, 0.1))
    sup.observe(a, b)
    assert sup.check(a, b) == RULE_STOPPED


def test_rule_4_suppresses_an_identity_switch(cfg):
    """200 km/h on urban footage is a teleporting track, not a fast vehicle."""
    sup = SuppressionEngine(cfg, max_range_m=100.0)
    a = _sample(1, "car", (10.0, 20.0), (0.0, 60.0))   # 216 km/h
    b = _sample(2, "car", (10.0, 40.0), (0.0, -5.0))
    sup.observe(a, b)
    assert sup.check(a, b) == RULE_SPEED_SANITY


def test_rule_5_suppresses_the_far_field(cfg):
    """Beyond max_range_m a one-pixel error is worth tens of metres."""
    sup = SuppressionEngine(cfg, max_range_m=45.0)
    a = _sample(1, "car", (10.0, 200.0), (0.0, 8.0))
    b = _sample(2, "car", (12.0, 210.0), (0.0, -8.0))
    sup.observe(a, b)
    assert sup.check(a, b) is not None


def test_rule_1_requires_a_real_closing_speed(cfg):
    """Jitter creates a phantom closing component; a real conflict does not."""
    sup = SuppressionEngine(cfg, max_range_m=100.0)
    a = _sample(1, "car", (10.0, 20.0), (0.0, 6.0))
    b = _sample(2, "car", (10.0, 30.0), (0.0, 5.9))   # barely closing
    sup.observe(a, b)
    assert sup.check(a, b) == RULE_VELOCITY_NOISE


# -- toggles ----------------------------------------------------------------


def test_every_rule_can_be_switched_off_independently(cfg):
    """Acceptance criterion: the rules must be individually toggleable, so that
    what each one removes can be SHOWN rather than asserted."""
    from edge.conflicts.suppression import ALL_RULES

    for rule in ALL_RULES:
        one_off = load_config(overrides=["suppression.{}=false".format(rule)])
        sup = SuppressionEngine(one_off, max_range_m=45.0)
        assert sup.enabled[rule] is False
        assert all(sup.enabled[other] for other in ALL_RULES if other != rule)


def test_disabled_rule_stops_firing(cfg):
    sup_on = SuppressionEngine(cfg, max_range_m=100.0)
    a = _sample(1, "car", (10.0, 20.0), (0.0, 0.2))
    b = _sample(2, "car", (10.0, 24.0), (0.0, 0.1))
    sup_on.observe(a, b)
    assert sup_on.check(a, b) == RULE_STOPPED

    off = load_config(overrides=["suppression.stopped_vehicles=false"])
    sup_off = SuppressionEngine(off, max_range_m=100.0)
    sup_off.observe(a, b)
    assert sup_off.check(a, b) != RULE_STOPPED


# -- regressions found by running the real demo clip ------------------------


def test_rule_2_fires_before_it_has_a_full_window(cfg):
    """Rule 2 must not wait for history before it may suppress.

    Found on the demo clip. The debouncer opens an encounter on the very first
    frame a pair is seen and then locks in the minimum TTC it ever observes, so
    a rule that cannot speak until frame five never gets to speak at all: the
    encounter is already open and its minimum already recorded. Requiring a
    full window before firing left 34 events on a five-minute clip where 8 was
    the right answer, and 31 of the 34 were vehicles a full lane apart.
    """
    sup = SuppressionEngine(cfg, max_range_m=100.0)
    a = _sample(1, "car", (9.0, 20.0), (0.0, 12.0))
    b = _sample(2, "car", (12.5, 22.0), (0.0, 9.0))   # adjacent lane, parallel
    sup.observe(a, b)
    assert sup._lane_splitting(a, b), (
        "a parallel pair a full lane apart is adjacent-lane traffic on the "
        "evidence of one frame; waiting for a window lets the encounter open"
    )


def test_rule_2_survives_a_noisy_heading_on_the_slower_vehicle(cfg):
    """Heading is a velocity direction, so at low speed it is not a measurement.

    Found on the demo clip: a car crawling in congestion showed headings
    scattered over 50 degrees between frames. Comparing that against a clean
    heading failed the parallelism test, rule 2 stood down, and a car passing
    cleanly in the next lane was reported as a severe conflict. Two cars are
    modelled as 2.0 m circles, so on a 3.5 m lane they always overlap - without
    rule 2 every adjacent-lane overtake becomes a near-miss.
    """
    sup = SuppressionEngine(cfg, max_range_m=100.0)
    for frame in range(8):
        t = frame * 0.033
        crawling = _sample(1, "car", (9.0, 20.0 + 0.5 * t), (0.05, 0.5), frame, t)
        # Heading is garbage at 0.5 m/s; the passing car's is clean.
        passing = _sample(2, "car", (12.6, 18.0 + 12.0 * t), (0.0, 12.0), frame, t)
        sup.observe(crawling, passing)
    assert sup._lane_splitting(crawling, passing)
