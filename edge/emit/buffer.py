"""Offline buffer for outbound events. Owner B.

The point of the edge tier is that a few hundred bytes per conflict leave the
roadside and the video stays where it was captured. That only holds up if the
link going down loses nothing.

Events are written to SQLite before any upload is attempted. A successful POST
marks them sent; a failure leaves them queued. Nothing is lost when the server
dies mid-run, which is a rehearsed demo beat rather than a bullet point:
kill D's server, let events pile up, restart it, watch them replay.

**Idempotency.** ``event_id`` is the primary key and inserts use
``INSERT OR IGNORE``, so the same event queued twice is stored once. Combined
with the server's own idempotency on ``event_id`` (contracts/api.md), replaying
a batch cannot double-count at either end. That matters because the retry path
is deliberately dumb: on an ambiguous failure it re-sends rather than trying to
work out whether the server got it.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id   TEXT PRIMARY KEY,
    t_video_s  REAL NOT NULL,
    payload    TEXT NOT NULL,
    sent       INTEGER NOT NULL DEFAULT 0,
    attempts   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_pending ON events(sent, t_video_s);
"""


class EventBuffer:
    """SQLite-backed queue of events awaiting upload."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "EventBuffer":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- writing -----------------------------------------------------------

    def add(self, events: Iterable[dict[str, Any]]) -> int:
        """Queue events. Returns how many were newly stored.

        Re-queuing an event already in the buffer is a no-op, not an error, and
        not a duplicate.
        """
        rows = [
            (
                e["event_id"],
                float(e["t_video_s"]),
                json.dumps(e, separators=(",", ":")),
            )
            for e in events
        ]
        if not rows:
            return 0
        before = self.total()
        self.conn.executemany(
            "INSERT OR IGNORE INTO events (event_id, t_video_s, payload) VALUES (?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return self.total() - before

    def mark_sent(self, event_ids: Iterable[str]) -> None:
        ids = [(eid,) for eid in event_ids]
        if not ids:
            return
        self.conn.executemany("UPDATE events SET sent = 1 WHERE event_id = ?", ids)
        self.conn.commit()

    def record_attempt(self, event_ids: Iterable[str]) -> None:
        ids = [(eid,) for eid in event_ids]
        if not ids:
            return
        self.conn.executemany(
            "UPDATE events SET attempts = attempts + 1 WHERE event_id = ?", ids
        )
        self.conn.commit()

    # -- reading -----------------------------------------------------------

    def pending(self, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT payload FROM events WHERE sent = 0 ORDER BY t_video_s, event_id"
        if limit is not None:
            sql += " LIMIT {}".format(int(limit))
        return [json.loads(row[0]) for row in self.conn.execute(sql)]

    def pending_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM events WHERE sent = 0").fetchone()[0])

    def sent_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM events WHERE sent = 1").fetchone()[0])

    def total(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
