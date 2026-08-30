"""Track hygiene: length, stationarity and switch screening. Owner A."""

from __future__ import annotations

from edge.track.hygiene import (
    drop_short_tracks,
    drop_stationary_tracks,
    measure_switch_rate,
)


def _row(track_id, frame, x, y, w=80.0, h=60.0, fps=24.0):
    return {
        "frame": frame,
        "t": round(frame / fps, 3),
        "track_id": track_id,
        "cls": "car",
        "bbox": [x - w / 2, y - h, x + w / 2, y],
        "conf": 0.8,
    }


def _track(track_id, n, x0, y0, dx, dy, **kw):
    return [_row(track_id, i, x0 + dx * i, y0 + dy * i, **kw) for i in range(n)]


# -- length -----------------------------------------------------------------


def test_short_tracks_are_dropped():
    rows = _track(1, 20, 100, 500, 5, 0) + _track(2, 3, 400, 500, 5, 0)
    kept, dropped = drop_short_tracks(rows, 5)
    assert dropped == 1
    assert {r["track_id"] for r in kept} == {1}


# -- stationarity -----------------------------------------------------------


def test_scenery_held_for_a_long_time_is_dropped():
    """The real case from the BeamNG clip: a patch of treeline detected as a
    car and held perfectly still for 75 frames."""
    rows = _track(9, 75, 398, 356, 0.01, 0.01)
    kept, dropped = drop_stationary_tracks(rows)
    assert dropped == [9]
    assert kept == []


def test_a_briefly_seen_moving_vehicle_survives():
    """The trap this function fell into first time.

    A real vehicle glimpsed for half a second covers little ground in absolute
    terms. An earlier version tested total displacement against box size and
    deleted exactly such a track off the demo clip. The measure has to be a
    RATE, so that a short sighting of something moving is kept while a long
    sighting of something still is not.
    """
    rows = _track(10, 13, 1163, 523, 11.0, 2.6)   # ~0.5 s, ~140 px
    kept, dropped = drop_stationary_tracks(rows)
    assert dropped == [], "a moving vehicle must survive however briefly it is seen"
    assert len(kept) == 13


def test_stationarity_is_judged_relative_to_box_size():
    """A far vehicle is small and moves few pixels; a near one is large and
    moves many. An absolute pixel threshold cannot serve both."""
    far = _track(1, 30, 900, 300, 1.2, 0.2, w=20, h=14)      # small box, small steps
    near = _track(2, 30, 400, 900, 6.0, 1.0, w=200, h=150)   # large box, large steps
    kept, dropped = drop_stationary_tracks(far + near)
    assert dropped == []
    assert {r["track_id"] for r in kept} == {1, 2}


def test_a_single_frame_track_is_not_judged():
    """With no elapsed time there is no rate to measure, so it passes through
    and the length filter deals with it."""
    rows = [_row(3, 7, 500, 500)]
    kept, dropped = drop_stationary_tracks(rows)
    assert dropped == []
    assert len(kept) == 1


# -- switches ---------------------------------------------------------------


def test_a_teleporting_track_is_flagged():
    rows = _track(1, 10, 100, 500, 4, 0) + [_row(1, 10, 700, 500)]
    report = measure_switch_rate(rows)
    assert report.suspected_switches == 1


def test_a_normal_track_is_not_flagged():
    report = measure_switch_rate(_track(1, 40, 100, 500, 6, 1))
    assert report.suspected_switches == 0
    assert report.per_1000_frames == 0.0
