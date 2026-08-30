"""The ConflictEvent contract. Owner B.

Three of the parent PRD's hard rules are enforced in code rather than left to
discipline, because each is the kind of thing that gets violated at hour 21 by
someone in a hurry. These tests are what make that enforcement real.
"""

from __future__ import annotations

import json

import pytest

from edge.common.event import MAX_EVENT_BYTES, ConflictEvent, Vehicle


def _event(**over):
    kwargs = dict(
        video_id="junction_a_evening",
        video_start="2026-08-28T19:06:00",
        location=[13.0106, 74.7943],
        conflict_type="crossing conflict",
        ttc_s=0.72,
        pet_s=1.4,
        vehicle_a=Vehicle("motorcycle", 47.2),
        vehicle_b=Vehicle("car", 31.4),
        detection_quality=0.71,
        track_ids=[87, 92],
        t_video_s=41.33,
        min_ttc_frame=1247,
    )
    kwargs.update(over)
    return ConflictEvent.build(**kwargs)


# -- the three hard rules ---------------------------------------------------


def test_severity_is_derived_and_has_no_setter():
    """It must not be possible to hand-set severity to make a number look better."""
    severe = _event(ttc_s=0.79)
    conflict = _event(ttc_s=0.81)
    assert severe.severity == "severe"
    assert conflict.severity == "conflict"
    with pytest.raises(AttributeError):
        severe.severity = "conflict"


def test_conditions_is_null_on_the_edge():
    """Written only by the server (M6). Arriving populated from the edge is a bug."""
    event = _event()
    assert event.conditions is None
    assert event.to_dict()["conditions"] is None


def test_there_is_no_blame_or_fault_field():
    """We describe what happened. We do not assign responsibility, because we
    cannot verify it and being wrong harms a real person."""
    keys = json.dumps(_event().to_dict()).lower()
    for forbidden in ("blame", "fault", "responsib", "at_fault", "culprit", "offender"):
        assert forbidden not in keys


# -- size -------------------------------------------------------------------


def test_serialised_event_is_within_the_budget():
    assert _event().byte_size() <= MAX_EVENT_BYTES


def test_worst_case_field_values_still_fit():
    """Longest class names, three-digit speeds, longest conflict type."""
    worst = _event(
        conflict_type="crossing conflict",
        vehicle_a=Vehicle("motorcycle", 149.9),
        vehicle_b=Vehicle("motorcycle", 149.9),
        ttc_s=1.49, pet_s=9.99, t_video_s=3599.99, min_ttc_frame=899999,
        track_ids=[999999, 999998],
    )
    assert worst.byte_size() <= MAX_EVENT_BYTES, worst.to_json()


def test_fixture_events_are_all_within_budget(fixtures_dir):
    events = json.loads((fixtures_dir / "events.edge.sample.json").read_text(encoding="utf-8"))
    assert events
    for e in events:
        assert len(json.dumps(e, separators=(",", ":")).encode("utf-8")) <= MAX_EVENT_BYTES


# -- identity ---------------------------------------------------------------


def test_event_id_is_stable_across_track_ordering():
    """The pair is unordered, so (87, 92) and (92, 87) are the same encounter."""
    assert _event(track_ids=[87, 92]).event_id == _event(track_ids=[92, 87]).event_id


def test_event_id_is_deterministic_across_runs():
    """M7 needs idempotency across restarts and buffer replays, not just within
    one process. A counter cannot give that; a content hash can."""
    assert _event().event_id == _event().event_id
    assert _event(min_ttc_frame=1248).event_id != _event().event_id


def test_wall_clock_is_derived_from_video_time():
    """t is seconds from video start; the wall clock is attached only at emission."""
    event = _event(video_start="2026-08-28T19:06:00", t_video_s=41.33)
    assert event.time == "2026-08-28T19:06:41"


# -- validation -------------------------------------------------------------


def test_a_good_event_validates():
    assert _event().validate() == []


def test_direction_must_be_null_on_the_edge():
    """'Against flow' needs C's learned lane directions; the edge has no lane map."""
    event = _event()
    event.vehicle_a.direction = "against flow"
    assert any("direction" in p for p in event.validate())


def test_populated_conditions_fails_validation():
    event = _event()
    object.__setattr__(event, "conditions", {"light": "dark"})
    assert any("conditions" in p for p in event.validate())


def test_a_ttc_above_the_conflict_threshold_is_not_an_event():
    assert any("conflict threshold" in p for p in _event(ttc_s=2.0).validate())


def test_unknown_vehicle_class_fails_validation():
    assert any("unknown class" in p for p in _event(vehicle_a=Vehicle("tractor", 20.0)).validate())


def test_identical_track_ids_fail_validation():
    assert any("distinct" in p for p in _event(track_ids=[87, 87]).validate())


def test_out_of_range_detection_quality_fails_validation():
    assert any("detection_quality" in p for p in _event(detection_quality=1.4).validate())


def test_provenance_fields_are_present(fixtures_dir):
    """Amendment A3: without t_video_s, C's ground-truth comparison becomes manual."""
    for e in json.loads((fixtures_dir / "events.edge.sample.json").read_text(encoding="utf-8")):
        assert "track_ids" in e and "t_video_s" in e and "min_ttc_frame" in e
        assert len(e["track_ids"]) == 2
