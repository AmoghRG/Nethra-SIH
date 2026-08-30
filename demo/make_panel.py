"""Composite demo card: the frame, the plot, and the record. Owner F's directory.

    python -m demo.make_panel --video data/Road_traffic_video_1.mp4 \\
        --tracks out/demo/tracks_px.jsonl --events out/demo/events.jsonl \\
        --plot out/demo/ttc_plot.png --calib fixtures/calibration.demo_clip.json \\
        --out out/demo/panel.png

This is PRD demo-script point 3 made visible: the frame at minimum TTC beside
the TTC trace, with the actual ConflictEvent JSON printed next to it. The point
is not decoration - it is that the record a judge reads carries metres,
seconds and km/h, and is about 350 bytes, rather than being a picture of two
boxes touching.
"""

from __future__ import annotations

import argparse
import json
import textwrap
from collections import defaultdict
from pathlib import Path

from demo._common import (
    PROVISIONAL_NOTE,
    calibration_is_provisional,
    load_events,
    pick_event,
    severity_colour,
)
from edge.common.jsonl import load_jsonl


def main(argv: list[str] | None = None) -> int:
    import cv2
    import numpy as np

    p = argparse.ArgumentParser(description="Composite demo card (demo artefact)")
    p.add_argument("--video", required=True)
    p.add_argument("--tracks", required=True)
    p.add_argument("--events", required=True)
    p.add_argument("--plot", required=True, help="PNG from make_ttc_plot")
    p.add_argument("--out", required=True)
    p.add_argument("--calib", default=None)
    p.add_argument("--event-id", default=None)
    p.add_argument("--width", type=int, default=1920)
    args = p.parse_args(argv)

    event = pick_event(load_events(args.events), args.event_id)
    rows = load_jsonl(args.tracks)
    by_frame = defaultdict(list)
    for r in rows:
        by_frame[r["frame"]].append(r)

    cap = cv2.VideoCapture(args.video)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(event["min_ttc_frame"]))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit("could not read frame {}".format(event["min_ttc_frame"]))

    pair = tuple(event["track_ids"])
    for r in by_frame.get(event["min_ttc_frame"], []):
        x1, y1, x2, y2 = (int(v) for v in r["bbox"])
        hit = r["track_id"] in pair
        colour = severity_colour(event["ttc_s"]) if hit else (170, 170, 170)
        cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 4 if hit else 2)
        cv2.drawMarker(frame, ((x1 + x2) // 2, y2), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 18, 3)
        if hit:
            cv2.putText(frame, "#{} {:.0f} km/h".format(
                r["track_id"],
                event["vehicle_a"]["speed_kmh"] if r["track_id"] == pair[0]
                else event["vehicle_b"]["speed_kmh"]),
                (x1, max(y1 - 10, 24)), cv2.FONT_HERSHEY_SIMPLEX, 0.9, colour, 3, cv2.LINE_AA)

    half = args.width // 2
    frame = _fit(cv2, np, frame, half)
    plot = _fit(cv2, np, cv2.imread(args.plot), half)
    top = np.hstack([_pad(np, frame, max(frame.shape[0], plot.shape[0])),
                     _pad(np, plot, max(frame.shape[0], plot.shape[0]))])

    text = _text_panel(cv2, np, event, args.width, provisional=bool(
        args.calib and calibration_is_provisional(args.calib)))
    card = np.vstack([top, text])

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(args.out, card)
    print("wrote {} ({}x{})".format(args.out, card.shape[1], card.shape[0]))
    print("event {}  ttc {} s  {} bytes serialised".format(
        event["event_id"], event["ttc_s"],
        len(json.dumps(event, separators=(",", ":")).encode("utf-8"))))
    return 0


def _fit(cv2, np, img, width):
    h, w = img.shape[:2]
    return cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)


def _pad(np, img, height):
    if img.shape[0] >= height:
        return img[:height]
    pad = np.full((height - img.shape[0], img.shape[1], 3), 255, np.uint8)
    return np.vstack([img, pad])


def _text_panel(cv2, np, event, width, provisional):
    body = json.dumps(event, indent=2)
    lines = []
    for line in body.splitlines():
        lines.extend(textwrap.wrap(line, 78, subsequent_indent="    ") or [""])
    size = len(json.dumps(event, separators=(",", ":")).encode("utf-8"))

    header = [
        "ConflictEvent  -  {} bytes on the wire, the whole record that leaves the camera".format(size),
        "metres and seconds, not pixel overlap.  conditions is null: the server attaches weather and light.",
    ]
    rows = len(lines) + len(header) + (2 if provisional else 1)
    panel = np.full((26 * rows + 26, width, 3), 22, np.uint8)

    y = 30
    for h in header:
        cv2.putText(panel, h, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                    (120, 220, 255), 1, cv2.LINE_AA)
        y += 26
    y += 6
    for line in lines:
        cv2.putText(panel, line, (20, y), cv2.FONT_HERSHEY_DUPLEX, 0.52,
                    (235, 235, 235), 1, cv2.LINE_AA)
        y += 26
    if provisional:
        cv2.putText(panel, PROVISIONAL_NOTE, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (60, 170, 245), 2, cv2.LINE_AA)
    return panel


if __name__ == "__main__":
    raise SystemExit(main())
