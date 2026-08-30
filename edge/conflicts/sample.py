"""One track's state in one frame, as the conflict engine sees it. Owner B."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class TrackSample:
    """A single row of ``tracks_m.jsonl``, parsed once.

    Positions and velocities are numpy arrays in SI units, on the ground
    plane. ``speed_kmh`` from the file is deliberately not carried: km/h exists
    only at serialisation boundaries, and having both units in flight is how
    the classic silent unit bug gets in.
    """

    frame: int
    t: float
    track_id: int
    cls: str
    conf: float
    p: np.ndarray
    v: np.ndarray
    speed_mps: float
    heading_deg: float

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TrackSample":
        v = np.asarray(row["v_mps"], dtype=float)
        return cls(
            frame=int(row["frame"]),
            t=float(row["t"]),
            track_id=int(row["track_id"]),
            cls=str(row["cls"]),
            conf=float(row.get("conf", 1.0)),
            p=np.asarray(row["ground_m"], dtype=float),
            v=v,
            speed_mps=float(np.hypot(v[0], v[1])),
            heading_deg=float(row["heading_deg"]),
        )

    @property
    def range_m(self) -> float:
        """Distance from the ground-frame origin."""
        return float(np.hypot(self.p[0], self.p[1]))


def pair_key(a: TrackSample, b: TrackSample) -> tuple[int, int]:
    """Unordered, canonical key for a pair of tracks.

    Sorted so that (87, 92) and (92, 87) are the same encounter. Debouncing
    depends on this: without it one encounter is counted twice, once per
    ordering.
    """
    return (a.track_id, b.track_id) if a.track_id <= b.track_id else (b.track_id, a.track_id)
