"""ByteTrack identity association. Owner A.

Ultralytics ships ByteTrack; we use it as shipped, through ``model.track(...,
persist=True)``. The tracker config is written out from ``edge/config.yaml`` so
there is one place where thresholds live, not two.

**Do not change trackers mid-sprint.** If the switch rate disappoints, report
it honestly in the accuracy table. The integration cost of a swap at hour 12
will exceed the benefit every time.

**Why identity switches matter more here than in most projects.** In an
ordinary detection demo an ID switch is cosmetic. Here it is catastrophic: when
track 87 jumps to a vehicle 15 metres away, the smoothed velocity registers an
enormous step, and the conflict engine sees something apparently doing
200 km/h straight at another vehicle. One ID switch can manufacture one false
*severe* event, and severe events are the headline number.

That is why the projection stage drops any track exceeding
``geometry.max_speed_kmh`` outright, and why the switch rate is measured
(``hygiene.py``) rather than assumed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from edge.common.config import Config
from edge.common.threads import repin
from edge.detect.classes import coco_ids_for, map_class
from edge.detect.detector import INSTALL_HINT, Detection, load_model

TRACKER_YAML = """\
tracker_type: bytetrack
track_high_thresh: {track_thresh}
track_low_thresh: 0.1
new_track_thresh: {track_thresh}
track_buffer: {track_buffer}
match_thresh: {match_thresh}
fuse_score: True
"""


def write_tracker_config(cfg: Config, path: Path | None = None) -> Path:
    """Materialise a ByteTrack yaml from our config, so thresholds live once."""
    body = TRACKER_YAML.format(
        track_thresh=float(cfg.get("tracker.track_thresh")),
        track_buffer=int(cfg.get("tracker.track_buffer")),
        match_thresh=float(cfg.get("tracker.match_thresh")),
    )
    if path is None:
        path = Path(tempfile.gettempdir()) / "netra_bytetrack.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class TrackedDetection(Detection):
    """A detection that has been given an identity."""

    def __init__(self, cls: str, bbox: tuple[float, float, float, float], conf: float, track_id: int):
        super().__init__(cls, bbox, conf)
        self.track_id = track_id


class Tracker:
    """Ultralytics ByteTrack, driven frame by frame with ``persist=True``."""

    def __init__(self, cfg: Config, model: Any | None = None) -> None:
        self.cfg = cfg
        self.model = model if model is not None else load_model(cfg)
        self.imgsz = int(cfg.get("detector.imgsz"))
        self.conf = float(cfg.get("detector.conf"))
        self.iou = float(cfg.get("detector.iou"))
        try:
            import torch
            default_device = 0 if torch.cuda.is_available() else "cpu"
        except Exception:
            default_device = "cpu"
        self.device = cfg.get("detector.device", default=default_device)
        if self.device == "auto":
            try:
                import torch
                self.device = 0 if torch.cuda.is_available() else "cpu"
            except Exception:
                self.device = "cpu"
        self.class_ids = coco_ids_for(self.model.names)
        self.tracker_cfg = write_tracker_config(cfg)
        self.invocations = 0

    def update(self, frame: Any) -> list[TrackedDetection]:
        """Detections with identities for one frame."""
        self.invocations += 1
        results = self.model.track(
            frame,
            imgsz=self.imgsz,
            conf=self.conf,
            iou=self.iou,
            classes=self.class_ids,
            tracker=str(self.tracker_cfg),
            device=self.device,
            persist=True,
            verbose=False,
        )
        # Ultralytics resets torch's thread count during inference, so a pin
        # set before the run silently stops holding after the first frame.
        repin()
        return self._parse(results[0])

    def _parse(self, result: Any) -> list[TrackedDetection]:
        out: list[TrackedDetection] = []
        boxes = getattr(result, "boxes", None)
        if boxes is None or boxes.id is None:
            # No identities yet on the very first frames, or nothing detected.
            return out
        for box in boxes:
            if box.id is None:
                continue
            netra = map_class(self.model.names[int(box.cls[0])])
            if netra is None:
                continue
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
            out.append(
                TrackedDetection(netra, (x1, y1, x2, y2), float(box.conf[0]), int(box.id[0]))
            )
        return out
