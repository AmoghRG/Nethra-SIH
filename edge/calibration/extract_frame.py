"""M1 — pull a single frame out of the demo video. Owner B.

Step one of calibration: get one clear frame to click points on. Prefer a
frame with little traffic, so kerbs, pole bases and markings are visible.

Needs OpenCV, which is a detector extra. Imported inside the function so that
nothing else in owner B's lane depends on it.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def extract_frame(video: str | Path, out_path: str | Path, at_seconds: float = 0.0) -> tuple[int, int]:
    """Write one frame to disk. Returns its (width, height) in pixels."""
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "frame extraction needs OpenCV: pip install opencv-python"
        ) from exc

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError("could not open video: {}".format(video))
    try:
        if at_seconds > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, at_seconds * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(
                "could not read a frame at {} s from {}".format(at_seconds, video)
            )
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_path), frame)
        h, w = frame.shape[:2]
        return int(w), int(h)
    finally:
        cap.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract one frame for calibration")
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", default="out/calib_frame.jpg")
    parser.add_argument("--at", type=float, default=0.0, help="seconds into the video")
    args = parser.parse_args(argv)

    w, h = extract_frame(args.video, args.out, args.at)
    print("wrote {} ({}x{} px)".format(args.out, w, h))
    print("next: python -m edge.calibration.pick_points --frame {}".format(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
