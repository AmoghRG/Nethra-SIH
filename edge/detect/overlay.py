"""Overlay video: boxes, ids, classes, validity polygon. Owner A.

Build this early. It is the debugging tool for everything in owner A's lane
*and* it is demo material, and those are the same artefact. The 20 tracks that
have to be spot-checked for identity switches get checked against this.

Needs OpenCV, imported at the call site.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

#: Stable per-class colours (BGR), so the overlay reads the same every run.
CLASS_COLOURS = {
    "car": (80, 220, 80),
    "motorcycle": (60, 160, 255),
    "truck": (220, 160, 60),
    "bus": (200, 80, 220),
    "auto": (60, 230, 230),
    "person": (240, 240, 240),
}


class OverlayWriter:
    """Writes an annotated copy of the source video."""

    def __init__(
        self,
        out_path: str | Path,
        size: tuple[int, int],
        fps: float,
        valid_region_px: Sequence[Sequence[float]] | None = None,
    ) -> None:
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError("the overlay needs OpenCV: pip install opencv-python") from exc
        self._cv2 = cv2
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        self.writer = cv2.VideoWriter(
            str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size
        )
        self.valid_region_px = valid_region_px

    def write(self, frame: Any, detections: Sequence[Any], frame_no: int, gated: bool = False) -> None:
        cv2 = self._cv2
        import numpy as np

        img = frame.copy()

        if self.valid_region_px:
            poly = np.asarray(self.valid_region_px, dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(img, [poly], True, (0, 200, 255), 2)

        for det in detections:
            x1, y1, x2, y2 = (int(v) for v in det.bbox)
            colour = CLASS_COLOURS.get(det.cls, (200, 200, 200))
            cv2.rectangle(img, (x1, y1), (x2, y2), colour, 2)
            # The ground contact point the whole pipeline actually uses.
            cv2.drawMarker(
                img, ((x1 + x2) // 2, y2), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 10, 2
            )
            label = "{}{} {:.2f}".format(
                det.cls, " #" + str(det.track_id) if hasattr(det, "track_id") else "", det.conf
            )
            cv2.putText(img, label, (x1, max(y1 - 6, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)

        banner = "frame {}  |  {} tracked".format(frame_no, len(detections))
        if gated:
            banner += "  |  GATED (detector skipped)"
        cv2.putText(img, banner, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 2, cv2.LINE_AA)
        self.writer.write(img)

    def close(self) -> None:
        self.writer.release()

    def __enter__(self) -> "OverlayWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
