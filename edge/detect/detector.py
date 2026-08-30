"""YOLOv8n detection. Owner A.

Stock weights, stock thresholds from config, no training of any kind.

``ultralytics`` and ``cv2`` are imported inside the functions that need them.
That is deliberate: owner B's entire lane (projection, TTC, suppression,
debouncing, emission) and the whole test suite must run before anyone has
installed a detector, and a module-level import would make ``import
edge.anything`` fail on a laptop with only numpy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from edge.common.config import Config
from edge.common.threads import repin
from edge.detect.classes import coco_ids_for, map_class

INSTALL_HINT = (
    "the detector needs ultralytics and opencv-python:\n"
    "    pip install -r requirements.txt\n"
    "Owner B's lane and the test suite run without them."
)


@dataclass
class Detection:
    """One detection in one frame, in pixels."""

    cls: str
    bbox: tuple[float, float, float, float]
    conf: float


def load_model(cfg: Config) -> Any:
    """Load YOLOv8n. Raises with an actionable message if the extra is missing."""
    try:
        from ultralytics import YOLO
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(INSTALL_HINT) from exc
    return YOLO(str(cfg.get("detector.weights")))


class Detector:
    """Thin wrapper over YOLOv8n that speaks NETRA classes."""

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
        self.invocations = 0

    def detect(self, frame: Any) -> list[Detection]:
        """Detections for one frame, already filtered to the six classes."""
        self.invocations += 1
        results = self.model.predict(
            frame,
            imgsz=self.imgsz,
            conf=self.conf,
            iou=self.iou,
            classes=self.class_ids,
            device=self.device,
            verbose=False,
        )
        repin()
        return self._parse(results[0])

    def _parse(self, result: Any) -> list[Detection]:
        out: list[Detection] = []
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return out
        for box in boxes:
            name = self.model.names[int(box.cls[0])]
            netra = map_class(name)
            if netra is None:
                continue
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
            out.append(Detection(netra, (x1, y1, x2, y2), float(box.conf[0])))
        return out
