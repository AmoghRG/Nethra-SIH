"""The one command: video in, validated events on D's server. Owners A and B.

    # the whole chain, once the demo clip and the detector extras exist
    python -m edge.run_pipeline --video data/junction.mp4 \\
        --calib fixtures/calibration.json --emit http://localhost:8000/api/events

    # what runs today: fixtures only, no video, no detector, no network
    python -m edge.run_pipeline --dry-run

Four stages, each of which can be entered directly with ``--from`` so a fixture
can be dropped in at any seam:

    track      video           -> tracks_px.jsonl   (owner A, needs ultralytics)
    project    tracks_px.jsonl -> tracks_m.jsonl    (owner B)
    conflicts  tracks_m.jsonl  -> events.jsonl      (owner B)
    emit       events.jsonl    -> POST /api/events  (owner B)

With no ``--video`` the run starts at ``project`` against
``fixtures/tracks_px.sample.jsonl``. That is not a degraded mode - it is how
the pipeline was built and how it is tested, and it means the chain can be
demonstrated end to end with the network cable unplugged and the demo clip
still missing.

This command satisfies S1 in the parent PRD: one command, video in, events out,
no manual intervention between.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from edge.calibration.homography import load_calibration
from edge.calibration.project import project_tracks
from edge.common.config import add_config_args, config_from_args
from edge.common.jsonl import load_jsonl, read_jsonl, write_jsonl
from edge.conflicts.engine import ConflictEngine

STAGES = ("track", "project", "conflicts", "emit")

DEFAULT_FIXTURE_PX = "fixtures/tracks_px.sample.jsonl"


def _banner(title: str) -> None:
    print()
    print("=" * 68)
    print(title)
    print("=" * 68)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="NETRA edge pipeline: video in, ConflictEvents out",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--video", default=None, help="demo clip; omit to run on fixtures")
    parser.add_argument("--calib", default="fixtures/calibration.json")
    parser.add_argument("--from", dest="start", choices=STAGES, default=None)
    parser.add_argument("--tracks-px", default=None, help="input when starting at project")
    parser.add_argument("--tracks-m", default=None, help="input when starting at conflicts")
    parser.add_argument("--events", default=None, help="input when starting at emit")
    parser.add_argument("--outdir", default="out")
    parser.add_argument("--emit", default=None, help="D's POST /api/events URL")
    parser.add_argument("--dry-run", action="store_true", help="buffer events, no network")
    parser.add_argument("--overlay", default=None, help="write an annotated video (track stage)")
    parser.add_argument(
        "--threads", type=int, default=1,
        help=(
            "pin torch and OpenCV to N threads. Default 1: at 320x320 that is "
            "both the fastest setting measured and the one the acceptance "
            "criterion asks for. 0 leaves the runtime unrestricted."
        ),
    )
    parser.add_argument("--split", choices=("all", "first-half", "second-half"), default="all")
    parser.add_argument("--max-frames", type=int, default=0)
    add_config_args(parser)
    args = parser.parse_args(argv)

    cfg = config_from_args(args)
    calib = load_calibration(args.calib)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Where to start. Explicit --from wins; otherwise a video means the whole
    # chain and no video means the fixture path.
    start = args.start or ("track" if args.video else "project")
    if start == "track" and not args.video:
        parser.error("--from track needs --video")
    order = STAGES[STAGES.index(start):]

    px_path = args.tracks_px or (str(outdir / "tracks_px.jsonl") if args.video else DEFAULT_FIXTURE_PX)
    m_path = args.tracks_m or str(outdir / "tracks_m.jsonl")
    ev_path = args.events or str(outdir / "events.jsonl")

    print("calibration: {}  rms_error_m {}  max_range_m {}".format(
        calib.video_id, calib.rms_error_m, calib.max_range_m))
    print("stages: {}".format(" -> ".join(order)))
    if not args.video and start == "project":
        print("no --video: running the fixture path from {}".format(px_path))

    # -- track ------------------------------------------------------------
    if "track" in order:
        _banner("M2  detection and tracking  (owner A)")
        from edge.track import run_track

        stage_args = argparse.Namespace(
            video=args.video, out=px_path, calib=args.calib, overlay=args.overlay,
            gate=False, max_frames=args.max_frames, threads=args.threads,
            config=args.config, overrides=args.overrides,
        )
        run_track.run(stage_args)

    # -- project ----------------------------------------------------------
    if "project" in order:
        _banner("M1  ground projection  (owner B)")
        if not Path(px_path).exists():
            print("missing input: {}".format(px_path), file=sys.stderr)
            return 1
        rows, stats = project_tracks(read_jsonl(px_path), calib, cfg)
        write_jsonl(m_path, rows, header=(
            "tracks_m.jsonl - ground plane, metres. edge/calibration/project.py\n"
            "calibration {} rms_error_m {}".format(calib.video_id, calib.rms_error_m)
        ))
        print(stats.render())
        print("wrote {} rows to {}".format(len(rows), m_path))

    # -- conflicts --------------------------------------------------------
    if "conflicts" in order:
        _banner("M3  conflict engine  (owner B)")
        if not Path(m_path).exists():
            print("missing input: {}".format(m_path), file=sys.stderr)
            return 1
        from edge.conflicts.run_conflicts import apply_split

        rows, split_desc = apply_split(load_jsonl(m_path), args.split)
        print("input: {} rows, {}".format(len(rows), split_desc))
        engine = ConflictEngine(cfg, calib)
        events = engine.run(rows)
        print(engine.stats.render(engine.suppression))
        write_jsonl(ev_path, [e.to_dict() for e in events], header=(
            "events.jsonl - ConflictEvent records, conditions left null for the server.\n"
            "source {} | split {}".format(Path(m_path).name, split_desc)
        ))
        print("wrote {} events to {}".format(len(events), ev_path))
        if events:
            print("largest serialised event: {} bytes (limit 400)".format(
                max(e.byte_size() for e in events)))

    # -- emit -------------------------------------------------------------
    if "emit" in order:
        _banner("M7  emission  (owner B)")
        if not Path(ev_path).exists():
            print("missing input: {}".format(ev_path), file=sys.stderr)
            return 1
        from edge.emit.uploader import Uploader

        target = None if args.dry_run else args.emit
        if target is None and not args.dry_run:
            print("no --emit target given; buffering only (same as --dry-run)")
        up = Uploader(cfg, target)
        try:
            up.submit_all(load_jsonl(ev_path))
            print(up.render())
            if up.oversize:
                print("OVERSIZE: " + ", ".join(up.oversize[:10]))
        finally:
            up.close()

    _banner("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
