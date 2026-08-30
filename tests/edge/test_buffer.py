"""The offline buffer and the uploader. Owner B.

The demo beat this protects: kill D's server mid-run, let events accumulate,
restart it, watch them replay. Fifteen seconds of stage time that proves an
architectural claim which would otherwise be a bullet point.
"""

from __future__ import annotations

import json

import pytest

from edge.common.config import load_config
from edge.emit.buffer import EventBuffer
from edge.emit.uploader import Uploader


@pytest.fixture
def events(fixtures_dir):
    return json.loads((fixtures_dir / "events.edge.sample.json").read_text(encoding="utf-8"))


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "buffer.db")


def _uploader(db, sending: bool, calls=None):
    def post(target, batch, timeout):
        if calls is not None:
            calls.append(len(batch))
        return sending

    up = Uploader(load_config(), "http://example.invalid/api/events", post=post, buffer_path=db)
    up.backoff_initial_s = 0.0
    up.backoff_max_s = -1.0   # do not sleep in a test
    return up


# -- the buffer itself ------------------------------------------------------


def test_add_is_idempotent_on_event_id(db, events):
    with EventBuffer(db) as buf:
        assert buf.add(events) == len(events)
        assert buf.add(events) == 0, "re-queuing the same events must not duplicate them"
        assert buf.total() == len(events)


def test_marking_sent_moves_events_out_of_pending(db, events):
    with EventBuffer(db) as buf:
        buf.add(events)
        assert buf.pending_count() == len(events)
        buf.mark_sent([e["event_id"] for e in events])
        assert buf.pending_count() == 0
        assert buf.sent_count() == len(events)


def test_pending_is_ordered_by_video_time(db, events):
    with EventBuffer(db) as buf:
        buf.add(list(reversed(events)))
        times = [e["t_video_s"] for e in buf.pending()]
        assert times == sorted(times)


def test_buffer_survives_reopening(db, events):
    with EventBuffer(db) as buf:
        buf.add(events)
    with EventBuffer(db) as reopened:
        assert reopened.pending_count() == len(events)


# -- the uploader -----------------------------------------------------------


def test_events_are_buffered_before_any_upload_is_attempted(db, events):
    """Buffer first, then send. Nothing can be lost between the two."""
    up = _uploader(db, sending=False)
    up.submit_all(events)
    assert up.buffer.total() == len(events)
    assert up.buffer.pending_count() == len(events)
    up.close()


def test_a_dead_server_loses_nothing_and_a_live_one_drains_it(db, events):
    calls: list[int] = []
    up = _uploader(db, sending=False, calls=calls)
    up.submit_all(events)
    assert up.buffer.pending_count() == len(events)
    assert up.buffer.sent_count() == 0

    up._post = lambda target, batch, timeout: True   # server comes back
    sent = up.drain()
    assert sent == len(events)
    assert up.buffer.pending_count() == 0
    assert up.buffer.sent_count() == len(events)
    up.close()


def test_replaying_the_buffer_twice_does_not_double_count(db, events):
    up = _uploader(db, sending=True)
    up.submit_all(events)
    assert up.drain() == 0, "nothing is left pending after a successful send"
    up.buffer.add(events)   # the same events arrive again
    assert up.buffer.total() == len(events)
    up.close()


def test_batches_are_capped_at_the_configured_size(db, events):
    calls: list[int] = []
    up = _uploader(db, sending=True, calls=calls)
    up.max_events = 2
    up.submit_all(events)
    assert calls, "something must have been posted"
    assert max(calls) <= 2
    up.close()


def test_an_oversize_event_is_flagged_before_the_wire(db, events):
    """The 400-byte figure is a claim we make out loud about what an edge link
    has to carry, so it is checked at the last point before sending too."""
    up = _uploader(db, sending=True)
    fat = dict(events[0])
    fat["event_id"] = "evt_deadbeef"
    fat["padding"] = "x" * 500
    up.submit(fat)
    up.flush()
    assert "evt_deadbeef" in up.oversize
    up.close()


def test_dry_run_never_calls_the_network(db, events):
    """Testable before D's server exists at all."""
    up = Uploader(load_config(), None, buffer_path=db)
    up.submit_all(events)
    assert up.buffer.pending_count() == len(events)
    assert up.batches_sent == 0
    up.close()
