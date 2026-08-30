"""M1 — the interactive point picker, and the escape hatch from it. Owner B.

Click reference points on the extracted frame, type each one's ground
coordinate in metres, and write ``calibration.json``.

**The cut line is built into this file.** If the picker is not working by hour
5, it is not on the critical path: ``--points`` accepts the correspondences on
the command line or from a JSON file, and no OpenCV window is ever opened. The
demo needs correct numbers, not a reusable tool.

**Getting real distances without visiting the site.** No tape measure, no site
access, only footage. In order of preference:

===================  =====================================================
Satellite imagery    Best available. Measure between fixed features on a
                     mapping tool. Kerb corners and pole bases work; anything
                     that moves does not.
Lane width           Indian urban lanes typically run 3.0–3.5 m. VERIFY
                     AGAINST IRC GUIDANCE rather than trusting that range —
                     it varies by road class, and the error propagates into
                     every speed reported.
Vehicle length       Hatchback roughly 3.8–4.0 m, auto-rickshaw roughly
                     2.6 m, motorcycle roughly 2.0 m. Measure across ten
                     stationary vehicles and average.
Road markings        Situational. Zebra pitch or dash length, IF the markings
                     follow a standard you can confirm for that road.
===================  =====================================================

Whichever is used, ``--method`` records it and its assumed uncertainty into
``calibration.json``. A judge asking "how do you know that is 3.5 metres?"
then gets a real answer instead of a shrug, and that is one of the more likely
questions you will face.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from edge.calibration.homography import (
    MAX_ACCEPTABLE_RMS_M,
    build_calibration_dict,
    fit_with_validation,
)

POINT_GUIDANCE = """
Point selection, in order of importance:

  1. Four points at the corners of a WIDE quadrilateral beat eight points
     bunched in the middle.
  2. Prefer the near and middle field. Avoid anything near the horizon: those
     points dominate the fit and are exactly where the projection is least
     trustworthy.
  3. All points must lie on the road SURFACE and be coplanar. Kerb corners and
     pole bases, not rooftops or sign faces.
  4. No three points collinear.
  5. Add a fifth point and mark it held_out. Without one, rms_error_m is
     structurally zero and tells you nothing.
""".strip()


def _pick_interactively(frame_path: str | Path) -> list[dict[str, Any]]:  # pragma: no cover
    """Click points on the frame, then type each ground coordinate."""
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "the interactive picker needs OpenCV: pip install opencv-python.\n"
            "Or skip it entirely and pass --points, which is the documented cut line."
        ) from exc

    img = cv2.imread(str(frame_path))
    if img is None:
        raise RuntimeError("could not read frame: {}".format(frame_path))

    clicks: list[tuple[int, int]] = []

    def on_mouse(event: int, x: int, y: int, flags: int, param: Any) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            clicks.append((x, y))
            cv2.drawMarker(img, (x, y), (0, 0, 255), cv2.MARKER_CROSS, 14, 2)
            cv2.putText(
                img, str(len(clicks)), (x + 8, y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2,
            )
            cv2.imshow("NETRA calibration - click points, then press q", img)

    print(POINT_GUIDANCE)
    print("\nClick each reference point. Press q when done.\n")
    cv2.imshow("NETRA calibration - click points, then press q", img)
    cv2.setMouseCallback("NETRA calibration - click points, then press q", on_mouse)
    while True:
        if cv2.waitKey(20) & 0xFF == ord("q"):
            break
    cv2.destroyAllWindows()

    points: list[dict[str, Any]] = []
    for i, (x, y) in enumerate(clicks, start=1):
        print("point {} at pixel ({}, {})".format(i, x, y))
        gx = float(input("  ground X (metres, east):  "))
        gy = float(input("  ground Y (metres, north): "))
        note = input("  note (what is this feature?): ").strip()
        held = input("  hold out of the fit for validation? [y/N]: ").strip().lower()
        rec: dict[str, Any] = {"pixel": [x, y], "ground_m": [gx, gy], "note": note}
        if held == "y":
            rec["held_out"] = True
        points.append(rec)
    return points


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M1 calibration: build calibration.json (owner B)",
        epilog=POINT_GUIDANCE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--frame", default=None, help="frame image to click on")
    parser.add_argument(
        "--points",
        default=None,
        help="JSON file of reference_points - the cut line, no OpenCV needed",
    )
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--out", default="out/calibration.json")
    parser.add_argument(
        "--location", nargs=2, type=float, required=True, metavar=("LAT", "LON")
    )
    parser.add_argument(
        "--valid-region",
        default=None,
        help="JSON list of [x,y] pixel points bounding the trusted area (A2)",
    )
    parser.add_argument("--max-range-m", type=float, default=45.0)
    parser.add_argument(
        "--method",
        default="vehicle_length",
        choices=["satellite", "lane_width", "vehicle_length", "road_markings", "site_measurement", "synthetic"],
    )
    parser.add_argument("--uncertainty-m", type=float, default=0.15)
    parser.add_argument("--method-note", default="")
    parser.add_argument("--image-size", nargs=2, type=int, default=None, metavar=("W", "H"))
    args = parser.parse_args(argv)

    if args.points:
        points = json.loads(Path(args.points).read_text(encoding="utf-8"))
    elif args.frame:
        points = _pick_interactively(args.frame)
    else:
        parser.error("give --frame to pick interactively, or --points to skip the picker")

    _, rms, mode = fit_with_validation(points)
    print("fit: {} points, validation={}, rms_error_m={:.3f}".format(len(points), mode, rms))
    if mode == "unvalidated":
        print(
            "WARNING: exactly four points and none held out. The fit is exact by\n"
            "construction, so rms_error_m is 0.0 and MEANS NOTHING. Add a fifth\n"
            "point marked held_out before you quote an error figure."
        )
    elif rms > MAX_ACCEPTABLE_RMS_M:
        print("rms above {} m - re-pick the points.".format(MAX_ACCEPTABLE_RMS_M))

    if args.valid_region:
        region = json.loads(args.valid_region)
    else:
        xs = [p["pixel"][0] for p in points]
        ys = [p["pixel"][1] for p in points]
        region = [
            [min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]
        ]
        print(
            "no --valid-region given; falling back to the bounding box of the\n"
            "reference points. Draw a real polygon before trusting far-field events."
        )

    payload = build_calibration_dict(
        video_id=args.video_id,
        reference_points=points,
        location=args.location,
        valid_region_px=region,
        max_range_m=args.max_range_m,
        method={
            "technique": args.method,
            "assumed_uncertainty_m": args.uncertainty_m,
            "note": args.method_note,
        },
        image_size_px=args.image_size,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("wrote {}".format(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
