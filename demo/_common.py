"""Shared helpers for the demo artefact scripts.

Owner F's directory by parent PRD 8; see contracts/README.md for the recorded
deviation. These are presentation scripts, not pipeline code - nothing in
``edge/`` imports them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from edge.common.jsonl import load_jsonl

#: Severity colours, BGR for OpenCV. Amber for a conflict, red for severe.
CONFLICT_BGR = (60, 170, 245)
SEVERE_BGR = (60, 60, 235)
CLEAR_BGR = (150, 220, 150)

#: Shown on any artefact built from a calibration whose scale is assumed rather
#: than measured. Better a visible caveat than a number a judge takes on trust.
PROVISIONAL_NOTE = "SCALE PROVISIONAL - distances and speeds are indicative"


def severity_colour(ttc_s: float | None, severe_s: float = 0.8, conflict_s: float = 1.5):
    if ttc_s is None:
        return CLEAR_BGR
    if ttc_s < severe_s:
        return SEVERE_BGR
    if ttc_s < conflict_s:
        return CONFLICT_BGR
    return CLEAR_BGR


def load_events(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return load_jsonl(path)


def pick_event(events: list[dict[str, Any]], event_id: str | None = None) -> dict[str, Any]:
    """The event to build the artefact around: named, or else the most severe.

    Lowest TTC wins, but **a TTC of exactly zero is skipped**. Zero means the
    two circles were already overlapping when the pair was first evaluated, the
    case ``ttc.py`` flags as suspicious rather than trusting. It is usually a
    projection artefact or a pair already past each other, it has no approach
    to plot, and it is the last thing to put in front of a judge. If every
    event is zero, fall back to one so the artefact still builds.
    """
    if not events:
        raise SystemExit("no events to build a demo from")
    if event_id:
        for e in events:
            if e["event_id"] == event_id:
                return e
        raise SystemExit("no such event: {}".format(event_id))
    approaching = [e for e in events if e["ttc_s"] > 0]
    return min(approaching or events, key=lambda e: e["ttc_s"])


def trace_for_pair(trace: list[dict[str, Any]], track_a: int, track_b: int) -> list[dict[str, Any]]:
    """Every trace row for one unordered pair, in time order."""
    lo, hi = min(track_a, track_b), max(track_a, track_b)
    rows = [r for r in trace if r["track_a"] == lo and r["track_b"] == hi]
    rows.sort(key=lambda r: r["frame"])
    return rows


def calibration_is_provisional(calib_path: str | Path) -> bool:
    """True when the calibration's own method note admits an assumed scale."""
    try:
        data = json.loads(Path(calib_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    note = (data.get("method", {}).get("note") or "").lower()
    return "provisional" in note or "assumed" in note
