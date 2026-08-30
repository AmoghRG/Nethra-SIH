"""M3 — encounter grouping, suppression rule 6. Owner B.

At 25 FPS a single two-second encounter produces about fifty conflict
readings. Emitted raw, "we detected 200 conflicts" means "we detected four
conflicts, fifty times each", and any judge who asks how you counted will find
it.

So: group readings by unordered track pair, and emit **one** event per
encounter carrying the *minimum* TTC observed and the frame it happened on.
Close an encounter when the pair separates, or after ``debounce_s`` with no
conflict reading.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from edge.conflicts.sample import TrackSample, pair_key


@dataclass
class Encounter:
    """One conflict between one pair of vehicles, however many frames it spans."""

    key: tuple[int, int]
    first_t: float
    last_t: float
    readings: int = 0
    min_ttc_s: float = float("inf")
    min_ttc_frame: int = -1
    min_ttc_t: float = -1.0
    a_at_min: TrackSample | None = None
    b_at_min: TrackSample | None = None
    conf_sum: float = 0.0
    conf_n: int = 0
    suspicious_readings: int = 0

    @property
    def detection_quality(self) -> float:
        """Mean detector confidence across both tracks over the encounter.

        Carried on every event so D can normalise conflict rates by it.
        Detection degrades in rain and darkness, so raw conflict counts fall
        in bad weather for entirely the wrong reason; ignore that and you
        conclude rain is safe, which is nonsense.
        """
        return self.conf_sum / self.conf_n if self.conf_n else 0.0

    def add(self, a: TrackSample, b: TrackSample, ttc_s: float, suspicious: bool) -> None:
        self.readings += 1
        self.last_t = a.t
        self.conf_sum += a.conf + b.conf
        self.conf_n += 2
        if suspicious:
            self.suspicious_readings += 1
        if ttc_s < self.min_ttc_s:
            self.min_ttc_s = ttc_s
            self.min_ttc_frame = a.frame
            self.min_ttc_t = a.t
            self.a_at_min = a
            self.b_at_min = b


class Debouncer:
    """Accumulates conflict readings into encounters.

    With ``enabled=False`` every reading closes immediately as its own
    encounter. That is not a fallback, it is how the effect of rule 6 gets
    demonstrated: run once with it off, count the events, run again with it on.
    """

    def __init__(self, debounce_s: float, enabled: bool = True) -> None:
        self.debounce_s = float(debounce_s)
        self.enabled = enabled
        self._open: dict[tuple[int, int], Encounter] = {}

    def update(
        self, a: TrackSample, b: TrackSample, ttc_s: float, suspicious: bool = False
    ) -> list[Encounter]:
        """Record one conflict reading. Returns any encounters closed by it."""
        key = pair_key(a, b)

        if not self.enabled:
            enc = Encounter(key=key, first_t=a.t, last_t=a.t)
            enc.add(a, b, ttc_s, suspicious)
            return [enc]

        enc = self._open.get(key)
        if enc is None:
            enc = Encounter(key=key, first_t=a.t, last_t=a.t)
            self._open[key] = enc
        enc.add(a, b, ttc_s, suspicious)
        return []

    def close_stale(self, now_t: float) -> list[Encounter]:
        """Close encounters with no conflict reading for ``debounce_s``."""
        if not self.enabled:
            return []
        closed = [
            enc for enc in self._open.values() if now_t - enc.last_t > self.debounce_s
        ]
        for enc in closed:
            self._open.pop(enc.key, None)
        return closed

    def flush(self) -> list[Encounter]:
        """Close everything still open. Called once at end of stream."""
        closed = list(self._open.values())
        self._open.clear()
        return closed
