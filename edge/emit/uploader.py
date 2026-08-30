"""Batching uploader for ConflictEvents. Owner B.

Batch every ``batch_max_seconds`` or ``batch_max_events``, whichever comes
first, and POST to D's ``/api/events``. Anything that fails goes to the SQLite
buffer and is retried with exponential backoff; anything already in the buffer
drains on the next success.

Run it against a dead port to demonstrate the buffer, then point it at a live
server and watch the queue empty:

    python -m edge.emit.uploader --events out/events.jsonl --target http://localhost:9/api/events
    python -m edge.emit.uploader --drain --target http://localhost:8000/api/events

``--dry-run`` skips the network entirely, which is how this is testable before
D's server exists at all.
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any, Callable, Iterable

from edge.common.config import Config, add_config_args, config_from_args
from edge.common.event import MAX_EVENT_BYTES
from edge.common.jsonl import load_jsonl
from edge.emit.buffer import EventBuffer


class Uploader:
    """Batches, posts, buffers and retries.

    ``post`` is injectable so the whole path is testable without a server and
    without monkeypatching a module-level import.
    """

    def __init__(
        self,
        cfg: Config,
        target: str | None,
        post: Callable[[str, list[dict[str, Any]], float], bool] | None = None,
        buffer_path: str | None = None,
    ) -> None:
        self.cfg = cfg
        self.target = target
        self.max_events = int(cfg.get("emit.batch_max_events"))
        self.max_seconds = float(cfg.get("emit.batch_max_seconds"))
        self.max_bytes = int(cfg.get("emit.max_event_bytes", MAX_EVENT_BYTES))
        self.timeout_s = float(cfg.get("emit.timeout_s"))
        self.backoff_initial_s = float(cfg.get("emit.backoff_initial_s"))
        self.backoff_max_s = float(cfg.get("emit.backoff_max_s"))
        self.buffer = EventBuffer(buffer_path or cfg.get("emit.buffer_db"))
        self._post = post or _post_json
        self._pending: list[dict[str, Any]] = []
        self._last_flush = time.monotonic()
        self.oversize: list[str] = []
        self.batches_sent = 0
        self.batches_failed = 0

    # -- ingestion ---------------------------------------------------------

    def submit(self, event: dict[str, Any]) -> None:
        """Queue one event, flushing when a batch trigger fires."""
        size = len(json.dumps(event, separators=(",", ":")).encode("utf-8"))
        if size > self.max_bytes:
            # Checked here as well as in the engine: this is the last point
            # before the wire, and the 400-byte figure is a claim we make out
            # loud about what an edge link has to carry.
            self.oversize.append(event["event_id"])
        self._pending.append(event)
        if len(self._pending) >= self.max_events:
            self.flush()
        elif time.monotonic() - self._last_flush >= self.max_seconds:
            self.flush()

    def submit_all(self, events: Iterable[dict[str, Any]]) -> None:
        for event in events:
            self.submit(event)
        self.flush()

    # -- sending -----------------------------------------------------------

    def flush(self) -> None:
        """Send the current batch, buffering it first so nothing can be lost."""
        batch, self._pending = self._pending, []
        self._last_flush = time.monotonic()
        if not batch:
            return
        self.buffer.add(batch)
        self._send(batch)

    def drain(self, max_batches: int | None = None) -> int:
        """Replay everything still queued. Returns the number sent."""
        sent = 0
        batches = 0
        while True:
            pending = self.buffer.pending(limit=self.max_events)
            if not pending:
                break
            if not self._send(pending):
                break
            sent += len(pending)
            batches += 1
            if max_batches is not None and batches >= max_batches:
                break
        return sent

    def _send(self, batch: list[dict[str, Any]]) -> bool:
        """One batch, with exponential backoff. True if the server took it."""
        if self.target is None:
            # Dry run: the buffer is the destination.
            return False

        ids = [e["event_id"] for e in batch]
        delay = self.backoff_initial_s
        while True:
            self.buffer.record_attempt(ids)
            if self._post(self.target, batch, self.timeout_s):
                self.buffer.mark_sent(ids)
                self.batches_sent += 1
                return True
            self.batches_failed += 1
            if delay > self.backoff_max_s:
                # Give up on this batch for now. It stays in the buffer and
                # will go out on the next drain, which is the whole point of
                # having a buffer rather than a retry loop.
                return False
            time.sleep(delay)
            delay *= 2

    def close(self) -> None:
        self.flush()
        self.buffer.close()

    def render(self) -> str:
        return "\n".join([
            "emit:",
            "  batches sent                {}".format(self.batches_sent),
            "  batch attempts failed       {}".format(self.batches_failed),
            "  buffered, still pending     {}".format(self.buffer.pending_count()),
            "  buffered, confirmed sent    {}".format(self.buffer.sent_count()),
            "  oversize events (>{} B)   {}".format(self.max_bytes, len(self.oversize)),
        ])


def _post_json(target: str, batch: list[dict[str, Any]], timeout_s: float) -> bool:
    """POST a batch. Any non-2xx, timeout or connection error is a failure.

    ``requests`` is imported here so that importing this module (and therefore
    running the tests) never requires it.
    """
    try:
        import requests
    except ImportError:  # pragma: no cover - depends on optional extra
        return False
    try:
        resp = requests.post(target, json=batch, timeout=timeout_s)
        return 200 <= resp.status_code < 300
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit ConflictEvents to the server (owner B)")
    parser.add_argument("--events", default=None, help="events.jsonl to send")
    parser.add_argument("--target", default=None, help="D's POST /api/events URL")
    parser.add_argument("--buffer-db", default=None, help="override emit.buffer_db")
    parser.add_argument("--drain", action="store_true", help="replay the buffer and exit")
    parser.add_argument("--dry-run", action="store_true", help="buffer only, no network")
    add_config_args(parser)
    args = parser.parse_args(argv)

    cfg = config_from_args(args)
    target = None if args.dry_run else args.target
    up = Uploader(cfg, target, buffer_path=args.buffer_db)
    try:
        if args.events:
            up.submit_all(load_jsonl(args.events))
        if args.drain:
            print("drained {} events".format(up.drain()))
        print(up.render())
        if up.oversize:
            print("OVERSIZE: " + ", ".join(up.oversize[:10]))
    finally:
        up.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
