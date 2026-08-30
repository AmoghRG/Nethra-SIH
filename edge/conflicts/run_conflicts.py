"""M3 CLI — tracks_m.jsonl in, events.jsonl out. Owner B.

    python -m edge.conflicts.run_conflicts \\
        --tracks out/tracks_m.jsonl --calib out/calibration.json --out out/events.jsonl

**The tuning trap, and why ``--split`` exists.**

C's ground-truth labels arrive around hour 14, and the temptation is to tune
thresholds until the numbers look good. Do not tune and report on the same
data. That is overfitting, and a judge with a research background will ask
precisely the right question about it.

So the split is enforced by the tool rather than by memory:

- ``--split first-half`` — the tuning set. Adjust thresholds here freely.
- ``--split second-half`` — held out. Touch it once, at hour 18, and report
  *that* number.

A slightly worse honest figure beats a better one you cannot defend.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from edge.calibration.homography import load_calibration
from edge.common.config import add_config_args, config_from_args
from edge.common.jsonl import load_jsonl, write_jsonl
from edge.conflicts.engine import ConflictEngine

SPLITS = ("all", "first-half", "second-half")


def apply_split(rows: list[dict], split: str) -> tuple[list[dict], str]:
    """Cut the track file in half by video time. Returns (rows, description)."""
    if split == "all" or not rows:
        return rows, "all"
    times = [float(r["t"]) for r in rows]
    midpoint = (min(times) + max(times)) / 2.0
    if split == "first-half":
        kept = [r for r in rows if float(r["t"]) < midpoint]
        return kept, "first half, t < {:.1f}s - TUNING SET".format(midpoint)
    kept = [r for r in rows if float(r["t"]) >= midpoint]
    return kept, "second half, t >= {:.1f}s - HELD OUT, report this one".format(midpoint)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M3 conflict engine (owner B)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--tracks", required=True, help="input tracks_m.jsonl")
    parser.add_argument(
        "--calib",
        default="fixtures/calibration.json",
        help="calibration.json - supplies video_id, location and max_range_m",
    )
    parser.add_argument("--out", default=None, help="output events.jsonl")
    parser.add_argument("--split", choices=SPLITS, default="all")
    parser.add_argument(
        "--trace", default=None,
        help="write a per-pair-frame trace here (debug and demo output; off by default)",
    )
    parser.add_argument(
        "--print-events", action="store_true", help="print each event as JSON"
    )
    add_config_args(parser)
    args = parser.parse_args(argv)

    cfg = config_from_args(args)
    calib = load_calibration(args.calib)

    rows, split_desc = apply_split(load_jsonl(args.tracks), args.split)
    print("input: {} ({} rows, {})".format(args.tracks, len(rows), split_desc))

    engine = ConflictEngine(cfg, calib, trace=bool(args.trace))
    events = engine.run(rows)
    print(engine.stats.render(engine.suppression))

    if args.print_events:
        for ev in events:
            print(ev.to_json())

    if args.trace:
        write_jsonl(args.trace, engine.trace, header=(
            "pair-frame trace - every pair the engine examined, with the rule "
            "that removed it. Debug and presentation output, not part of the "
            "edge loop."
        ))
        print("wrote {} trace rows to {}".format(len(engine.trace), args.trace))

    if args.out:
        write_jsonl(
            args.out,
            [e.to_dict() for e in events],
            header=(
                "events.jsonl - ConflictEvent records, conditions left null for the server.\n"
                "source {} | split {} | calibration rms_error_m {}".format(
                    Path(args.tracks).name, split_desc, calib.rms_error_m
                )
            ),
        )
        print("wrote {} events to {}".format(len(events), args.out))

    if events:
        largest = max(e.byte_size() for e in events)
        print("largest serialised event: {} bytes (limit 400)".format(largest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
