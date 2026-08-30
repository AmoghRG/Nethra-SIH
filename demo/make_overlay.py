"""Annotated overlay video: the thing you play on stage. Owner F's directory.

    python -m demo.make_overlay --video data/Road_traffic_video_5.mp4 \\
        --tracks out/sim/tracks_px.jsonl --out out/sim/overlay.mp4

With an events file and a trace it also draws the live TTC readout for the
conflicting pair, amber below 1.5 s and red below 0.8 s:

    python -m demo.make_overlay --video data/Road_traffic_video_1.mp4 \\
        --tracks out/demo/tracks_px.jsonl --events out/demo/events.jsonl \\
        --trace out/demo/trace.jsonl --calib fixtures/calibration.demo_clip.json \\
        --around-event --out out/demo/overlay.mp4

``--around-event`` renders only the seconds either side of the chosen conflict,
which is what you want from a five-minute source.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from demo._common import (
    PROVISIONAL_NOTE,
    calibration_is_provisional,
    load_events,
    pick_event,
    severity_colour,
    trace_for_pair,
)
from edge.common.jsonl import load_jsonl
from edge.detect.overlay import CLASS_COLOURS


def main(argv: list[str] | None = None) -> int:
    import cv2

    p = argparse.ArgumentParser(description="Annotated overlay video (demo artefact)")
    p.add_argument("--video", required=True)
    p.add_argument("--tracks", required=True, help="tracks_px.jsonl")
    p.add_argument("--out", required=True)
    p.add_argument("--events", default=None)
    p.add_argument("--trace", default=None)
    p.add_argument("--calib", default=None, help="only to detect a provisional scale")
    p.add_argument("--event-id", default=None)
    p.add_argument("--around-event", action="store_true")
    p.add_argument("--pad-s", type=float, default=2.5)
    args = p.parse_args(argv)

    rows = load_jsonl(args.tracks)
    by_frame = defaultdict(list)
    for r in rows:
        by_frame[r["frame"]].append(r)

    event = pair = ttc_by_frame = None
    if args.events:
        event = pick_event(load_events(args.events), args.event_id)
        pair = tuple(event["track_ids"])
        if args.trace:
            ttc_by_frame = {
                t["frame"]: t["ttc_s"]
                for t in trace_for_pair(load_jsonl(args.trace), *pair)
            }

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit("could not open video: {}".format(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

    lo, hi = 0, 10 ** 9
    if args.around_event and event:
        centre = event["min_ttc_frame"]
        lo = max(0, int(centre - args.pad_s * fps))
        hi = int(centre + args.pad_s * fps)

    provisional = args.calib and calibration_is_provisional(args.calib)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), fps, size)

    frame_no, written = 0, 0
    try:
        while True:
            ok, img = cap.read()
            if not ok:
                break
            if frame_no > hi:
                break
            if frame_no >= lo:
                _draw(cv2, img, by_frame.get(frame_no, []), frame_no, fps,
                      pair, ttc_by_frame, event, provisional)
                writer.write(img)
                written += 1
            frame_no += 1
    finally:
        cap.release()
        writer.release()

    print("wrote {} frames to {}".format(written, args.out))
    if event:
        print("built around {} (ttc {} s, tracks {})".format(
            event["event_id"], event["ttc_s"], event["track_ids"]))
    if provisional:
        print(PROVISIONAL_NOTE)
    return 0


def _draw(cv2, img, dets, frame_no, fps, pair, ttc_by_frame, event, provisional):
    h, w = img.shape[:2]
    scale = max(w / 1280.0, 0.6)
    ttc = ttc_by_frame.get(frame_no) if ttc_by_frame else None

    for r in dets:
        x1, y1, x2, y2 = (int(v) for v in r["bbox"])
        in_pair = pair is not None and r["track_id"] in pair
        colour = severity_colour(ttc) if in_pair else CLASS_COLOURS.get(r["cls"], (200, 200, 200))
        cv2.rectangle(img, (x1, y1), (x2, y2), colour, 3 if in_pair else 2)
        # The ground contact point the whole pipeline actually uses.
        cv2.drawMarker(img, ((x1 + x2) // 2, y2), (0, 0, 255),
                       cv2.MARKER_TILTED_CROSS, int(14 * scale), 2)
        cv2.putText(img, "{} #{}".format(r["cls"], r["track_id"]),
                    (x1, max(y1 - 6, int(16 * scale))),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55 * scale, colour, max(int(2 * scale), 1),
                    cv2.LINE_AA)

    bar = int(34 * scale)
    cv2.rectangle(img, (0, 0), (w, bar), (0, 0, 0), -1)
    cv2.putText(img, "NETRA  frame {}  t={:.2f}s".format(frame_no, frame_no / fps),
                (10, int(24 * scale)), cv2.FONT_HERSHEY_SIMPLEX, 0.7 * scale,
                (255, 255, 255), max(int(2 * scale), 1), cv2.LINE_AA)

    if ttc is not None:
        label = "TTC {:.2f} s".format(ttc)
        if event and ttc <= event["ttc_s"] + 1e-9:
            label += "   MINIMUM"
        cv2.putText(img, label, (w - int(330 * scale), int(24 * scale)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8 * scale, severity_colour(ttc),
                    max(int(2 * scale), 1), cv2.LINE_AA)
    elif pair is not None:
        cv2.putText(img, "TTC --", (w - int(330 * scale), int(24 * scale)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8 * scale, (180, 180, 180),
                    max(int(2 * scale), 1), cv2.LINE_AA)

    if provisional:
        cv2.putText(img, PROVISIONAL_NOTE, (10, h - int(12 * scale)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (60, 170, 245),
                    max(int(1 * scale), 1), cv2.LINE_AA)


if __name__ == "__main__":
    raise SystemExit(main())
