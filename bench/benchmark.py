"""M4 - the benchmark harness. Owner A. Priority P2.

    python -m bench.benchmark --video data/junction.mp4 --max-frames 500

Three configurations, one command:

    gate + 320   the two-tier design as intended
    plain 320    the cheap detector alone
    plain 640    what the accuracy tier costs

**Read this before writing any slide copy.**

We have no Raspberry Pi, no Hailo accelerator and no camera. This harness
therefore measures a **laptop CPU proxy**, and every table it emits carries
that label - hardcoded in :func:`render_table`, so no run can produce an
unlabelled figure. Quoting a Pi FPS number as measured would be caught by any
judge who owns one, and the whole deck would lose credibility with it.

What can and cannot be said without hardware splits cleanly:

- **The accelerator stage can be estimated.** Hailo's Dataflow Compiler
  profiler reports expected FPS from a compiled model with no device attached,
  and the Model Zoo publishes measured Hailo-8L figures for the YOLOv8 family.
  That is a citable, vendor-sourced number.
- **The CPU stages cannot be.** Decode, preprocessing, ByteTrack, projection
  and pairwise TTC all run on the Pi's CPU. Nothing simulates a Cortex-A76
  doing that work, and the pairwise conflict step grows quadratically with
  vehicle count.

If a Pi 4 can be borrowed from a lab, measure on it and report it as a floor.
Real hardware one generation old beats perfect emulation of the current one.

The motion gate is a **power and thermal contribution, not a throughput
rescue** - see ``edge/gate/motion_gate.py``. Present it as what it is.

**The harness is itself the deliverable.** It runs the moment a board arrives,
and offering it is a stronger position than a number nobody can check.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from edge.common.config import add_config_args, config_from_args
from edge.common.threads import current as pinned_threads, pin_threads
from edge.gate.motion_gate import MotionGate
from edge.track.tracker import Tracker

PROVENANCE = "laptop CPU proxy - Raspberry Pi figures pending hardware."

#: Design target on the Hailo-8L, stated as a target and never as a measurement.
DESIGN_TARGET_FPS = 30.0


@dataclass
class BenchResult:
    name: str
    imgsz: int
    gate: bool
    threads: object
    frames: int
    wall_s: float
    fps: float
    cpu_core_utilisation: float
    detector_invocations: int
    invocations_per_min: float

    @property
    def gate_reduction_pct(self) -> float:
        return 100.0 * (self.frames - self.detector_invocations) / self.frames if self.frames else 0.0


def run_config(
    video: str, cfg_path: str | None, name: str, imgsz: int, gate_on: bool,
    max_frames: int, threads: int = 1
) -> BenchResult:
    """One configuration, start to finish."""
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError("the benchmark needs OpenCV: pip install -r requirements.txt") from exc

    from edge.common.config import load_config

    pin_threads(threads)
    cfg = load_config(cfg_path)
    cfg.override("detector.imgsz", str(imgsz))
    cfg.override("gate.enabled", "true" if gate_on else "false")

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError("could not open video: {}".format(video))

    tracker = Tracker(cfg)
    gate = MotionGate(cfg)

    frames = 0
    t0 = time.perf_counter()
    cpu0 = time.process_time()
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if max_frames and frames >= max_frames:
                break
            if gate.should_detect(frame):
                tracker.update(frame)
            frames += 1
    finally:
        cap.release()

    wall = max(time.perf_counter() - t0, 1e-9)
    cpu = time.process_time() - cpu0
    return BenchResult(
        name=name,
        imgsz=imgsz,
        gate=gate_on,
        threads=pinned_threads(),
        frames=frames,
        wall_s=round(wall, 2),
        fps=round(frames / wall, 2),
        cpu_core_utilisation=round(cpu / wall, 2),
        detector_invocations=gate.stats.detector_invocations,
        invocations_per_min=round(60.0 * gate.stats.detector_invocations / wall, 1),
    )


def render_table(results: list[BenchResult]) -> str:
    """The table. The provenance label is not optional and not a parameter."""
    head = "{:<14} {:>6} {:>6} {:>8} {:>8} {:>8} {:>10} {:>10}".format(
        "config", "imgsz", "gate", "threads", "FPS", "cores", "FPS/core", "det/min"
    )
    lines = [head, "-" * len(head)]
    for r in results:
        lines.append("{:<14} {:>6} {:>6} {:>8} {:>8.2f} {:>8.2f} {:>10.2f} {:>10.1f}".format(
            r.name, r.imgsz, "on" if r.gate else "off", str(r.threads),
            r.fps, r.cpu_core_utilisation,
            r.fps / max(r.cpu_core_utilisation, 1e-9), r.invocations_per_min,
        ))

    gated = next((r for r in results if r.gate), None)
    if gated is not None:
        verdict = "PASS" if gated.gate_reduction_pct >= 40.0 else "below the 40% target"
        lines.append("")
        lines.append("motion gate reduced detector invocations by {:.1f}%  ({})".format(
            gated.gate_reduction_pct, verdict))
        lines.append("That is a POWER AND THERMAL figure. The gate saves energy when")
        lines.append("nothing is happening; it does nothing during a conflict, which is")
        lines.append("exactly when sustained full-speed inference is needed.")

    lines.append("")
    lines.append("Hailo-8L design target: {:.0f} FPS. NOT MEASURED - vendor-rated, "
                 "pending hardware.".format(DESIGN_TARGET_FPS))
    lines.append(PROVENANCE)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M4 benchmark harness (owner A)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--video", required=True)
    parser.add_argument("--max-frames", type=int, default=500)
    parser.add_argument(
        "--threads", type=int, default=1,
        help="pin torch and OpenCV to N threads. The acceptance criterion is "
             "per-core, so an unpinned figure does not answer it.",
    )
    parser.add_argument("--out", default="bench/results/benchmark.json")
    add_config_args(parser)
    args = parser.parse_args(argv)
    config_from_args(args)  # validate overrides before a long run

    configs = [
        ("gate + 320", 320, True),
        ("plain 320", 320, False),
        ("plain 640", 640, False),
    ]
    results = []
    for name, imgsz, gate_on in configs:
        print("running {} ...".format(name))
        results.append(run_config(
            args.video, args.config, name, imgsz, gate_on,
            args.max_frames, args.threads))

    table = render_table(results)
    print()
    print(table)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "video": args.video,
        "provenance": PROVENANCE,
        "hailo_design_target_fps": DESIGN_TARGET_FPS,
        "hailo_measured": None,
        "results": [asdict(r) for r in results],
    }, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".txt").write_text(table + "\n", encoding="utf-8")
    print()
    print("written to {} and {}".format(out, out.with_suffix(".txt")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
