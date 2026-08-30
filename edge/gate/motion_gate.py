"""M4 — the motion gate. Owner A. Priority P2.

**Get the framing right in words before writing any slide copy.**

The motion gate is a **power and thermal contribution, not a throughput
rescue.** It helps when nothing is happening. It does *not* help during a
conflict, which is precisely when sustained full-speed inference is needed. The
cheap detector must hold 15 FPS on its own whenever there is traffic.

Presenting the gate as the thing that makes real-time possible on cheap
hardware is an overstatement, and a judge who thinks it through will find the
hole. Its real value is lower average power, less heat in a sealed outdoor box,
and longer hardware life. Say those.

Every gate decision is logged, so the reduction in detector invocations is
measured rather than asserted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from edge.common.config import Config


@dataclass
class GateStats:
    frames: int = 0
    detector_invocations: int = 0

    @property
    def skipped(self) -> int:
        return self.frames - self.detector_invocations

    @property
    def reduction_pct(self) -> float:
        return 100.0 * self.skipped / self.frames if self.frames else 0.0

    def render(self) -> str:
        verdict = "PASS" if self.reduction_pct >= 40.0 else "below the 40% target"
        return "\n".join([
            "motion gate:",
            "  frames                      {}".format(self.frames),
            "  detector invocations        {}".format(self.detector_invocations),
            "  frames skipped              {}".format(self.skipped),
            "  invocation reduction        {:.1f}%   ({})".format(
                self.reduction_pct, verdict
            ),
            "  NOTE: this is a power and thermal figure, not a throughput one.",
            "  The gate saves energy when nothing is happening. It does nothing",
            "  during a conflict, which is when full-speed inference is needed.",
        ])


class MotionGate:
    """Frame differencing gate: skip the detector when the scene is still."""

    def __init__(self, cfg: Config) -> None:
        self.enabled = bool(cfg.get("gate.enabled"))
        self.downscale = int(cfg.get("gate.downscale"))
        self.threshold = int(cfg.get("gate.pixel_diff_threshold"))
        self.min_changed = float(cfg.get("gate.min_changed_fraction"))
        self.stats = GateStats()
        self._prev: Any = None

    def should_detect(self, frame: Any) -> bool:
        """True if the detector should run on this frame.

        Fails OPEN. If anything is off (gate disabled, no previous frame, a
        decode hiccup), the detector runs. A gate that wrongly skips a frame
        loses a conflict; a gate that wrongly runs one costs a few milliwatts.
        """
        self.stats.frames += 1
        if not self.enabled:
            self.stats.detector_invocations += 1
            return True

        try:
            import cv2
        except ImportError:  # pragma: no cover - depends on optional extra
            self.stats.detector_invocations += 1
            return True

        small = cv2.resize(
            frame,
            (max(frame.shape[1] // self.downscale, 1), max(frame.shape[0] // self.downscale, 1)),
            interpolation=cv2.INTER_AREA,
        )
        grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        if self._prev is None:
            self._prev = grey
            self.stats.detector_invocations += 1
            return True

        diff = cv2.absdiff(grey, self._prev)
        self._prev = grey
        changed = float((diff > self.threshold).sum()) / float(diff.size)

        run = changed >= self.min_changed
        if run:
            self.stats.detector_invocations += 1
        return run
