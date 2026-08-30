"""CPU thread pinning. Jointly owned by A and B.

Two separate reasons this exists, and both are load-bearing.

**1. The acceptance criterion is per-core.** Both PRDs ask for ">=15 FPS at
320x320 on ONE laptop CPU core". A figure produced by twenty cores says
nothing about a Raspberry Pi, and quoting it as though it did would be the
same mistake as quoting a Pi FPS number we never measured.

**2. Ultralytics resets the thread count during inference.** Setting
``torch.set_num_threads(1)`` before the run is not enough: the first
``predict``/``track`` call puts it back to 8, silently, and every measurement
after that is of something other than what was asked for. The pin therefore
has to be re-applied *after* each inference call, which is what
:func:`repin` is for.

**Measured on the demo clip (1920x1080, YOLOv8n at 320, this laptop):**

    threads        FPS     cores    FPS/core
    1             48.6      1.88        25.9
    2             65.7      1.92        34.1
    4             67.7      3.78        17.9
    unrestricted  32.1     17.96         1.8

Note the last row. Unrestricted is the *slowest* configuration: at 320x320 the
model is small enough that twenty cores spend more time coordinating than
computing. Left alone, the harness would have reported a worse number than the
one-core figure it is supposed to be stress-testing.
"""

from __future__ import annotations

#: Threads requested by the caller. 0 means leave the runtime alone.
_pinned = 0


def pin_threads(n: int) -> int:
    """Pin torch and OpenCV to ``n`` threads. Returns what was applied.

    Call once at start-up. Pass 0 to leave both unrestricted. For a figure that
    will be quoted against the acceptance criterion, pass 1 and also set
    ``OMP_NUM_THREADS=1`` in the environment, since the OpenMP pool is built at
    import time and cannot be resized afterwards.
    """
    global _pinned
    _pinned = max(int(n), 0)
    repin()
    try:
        import cv2

        cv2.setNumThreads(_pinned if _pinned else -1)
    except ImportError:
        pass
    return _pinned


def repin() -> None:
    """Re-apply the pin. Call after any inference that may have reset it."""
    if not _pinned:
        return
    try:
        import torch

        if torch.get_num_threads() != _pinned:
            torch.set_num_threads(_pinned)
    except (ImportError, RuntimeError):
        pass


def current() -> int | str:
    """What is pinned, for reporting alongside any figure."""
    return _pinned or "unrestricted"


def torch_threads() -> int | None:
    """What torch actually has right now, which is not always what was asked."""
    try:
        import torch

        return int(torch.get_num_threads())
    except (ImportError, RuntimeError):
        return None
