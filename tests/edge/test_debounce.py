"""Rule 6: one encounter, exactly one event. Owner B.

Without this, "we detected 200 conflicts" means "we detected four conflicts,
fifty times each", and any judge who asks how you counted will find it.
"""

from __future__ import annotations

from edge.common.config import load_config
from edge.common.jsonl import load_jsonl
from edge.conflicts.debounce import Debouncer
from edge.conflicts.engine import ConflictEngine
from edge.conflicts.sample import TrackSample, pair_key


def _sample(track_id, p, v, frame, t, conf=0.9):
    return TrackSample.from_row({
        "frame": frame, "t": t, "track_id": track_id, "cls": "car", "conf": conf,
        "ground_m": list(p), "v_mps": list(v), "speed_kmh": 0.0, "heading_deg": 0.0,
    })


def test_pair_key_is_unordered():
    """(87, 92) and (92, 87) must be the same encounter, or it is counted twice."""
    a = _sample(92, (0, 0), (0, 1), 0, 0.0)
    b = _sample(87, (0, 5), (0, -1), 0, 0.0)
    assert pair_key(a, b) == pair_key(b, a) == (87, 92)


def test_fifty_readings_become_one_event():
    d = Debouncer(debounce_s=3.0, enabled=True)
    for frame in range(50):
        t = frame * 0.04
        a = _sample(1, (0, 0), (0, 5), frame, t)
        b = _sample(2, (0, 10), (0, -5), frame, t)
        assert d.update(a, b, 1.0 - frame * 0.01) == [], "nothing closes mid-encounter"
    closed = d.flush()
    assert len(closed) == 1
    assert closed[0].readings == 50


def test_the_event_carries_the_minimum_ttc_and_its_frame():
    d = Debouncer(debounce_s=3.0, enabled=True)
    ttcs = [1.4, 0.9, 0.55, 0.8, 1.2]
    for frame, ttc in enumerate(ttcs):
        t = frame * 0.04
        d.update(_sample(1, (0, 0), (0, 5), frame, t),
                 _sample(2, (0, 10), (0, -5), frame, t), ttc)
    enc = d.flush()[0]
    assert enc.min_ttc_s == 0.55
    assert enc.min_ttc_frame == 2
    assert enc.min_ttc_t == 0.08


def test_an_encounter_closes_after_the_debounce_window():
    d = Debouncer(debounce_s=3.0, enabled=True)
    d.update(_sample(1, (0, 0), (0, 5), 0, 0.0), _sample(2, (0, 10), (0, -5), 0, 0.0), 1.0)
    assert d.close_stale(2.0) == [], "still inside the window"
    closed = d.close_stale(4.0)
    assert len(closed) == 1


def test_two_separate_encounters_between_the_same_pair_yield_two_events():
    d = Debouncer(debounce_s=3.0, enabled=True)
    d.update(_sample(1, (0, 0), (0, 5), 0, 0.0), _sample(2, (0, 10), (0, -5), 0, 0.0), 1.0)
    first = d.close_stale(5.0)
    d.update(_sample(1, (0, 0), (0, 5), 200, 8.0), _sample(2, (0, 10), (0, -5), 200, 8.0), 0.7)
    second = d.flush()
    assert len(first) == 1 and len(second) == 1


def test_disabling_debounce_emits_one_event_per_reading():
    """Not a fallback: this is how rule 6's effect gets demonstrated."""
    d = Debouncer(debounce_s=3.0, enabled=False)
    emitted = []
    for frame in range(20):
        emitted += d.update(_sample(1, (0, 0), (0, 5), frame, frame * 0.04),
                            _sample(2, (0, 10), (0, -5), frame, frame * 0.04), 1.0)
    assert len(emitted) == 20
    assert d.flush() == []


def test_detection_quality_averages_both_tracks(fixtures_dir):
    d = Debouncer(debounce_s=3.0, enabled=True)
    d.update(_sample(1, (0, 0), (0, 5), 0, 0.0, conf=0.6),
             _sample(2, (0, 10), (0, -5), 0, 0.0, conf=0.8), 1.0)
    enc = d.flush()[0]
    assert abs(enc.detection_quality - 0.7) < 1e-9


def test_engine_emits_one_event_per_encounter_on_the_sample_fixture(fixtures_dir, calib):
    """End to end: the sample scene has two encounters and yields two events."""
    engine = ConflictEngine(load_config(), calib)
    events = engine.run(load_jsonl(fixtures_dir / "tracks_m.sample.jsonl"))
    assert len(events) == engine.stats.encounters == 2
    assert engine.stats.conflict_readings > len(events), "debouncing must actually collapse readings"
    assert len({e.event_id for e in events}) == len(events), "event ids must be unique"
