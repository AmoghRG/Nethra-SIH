"""Track hygiene: length filtering and switch-rate measurement. Owner A.

Two acceptance criteria live here, and both are numbers we have to state out
loud rather than assume:

- fewer than **5 identity switches per 1,000 frames**
- no track shorter than ``tracker.min_track_frames`` in the output

Nothing in here needs OpenCV or ultralytics: it operates on rows, so it can be
run against a fixture.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

#: A jump larger than this between consecutive frames of one track is not a
#: vehicle moving, it is the identity having been handed to a different one.
#: In pixels, deliberately generous: this is a screen for the obvious cases, and
#: the authoritative check is the metres-per-second one in the projection stage.
SWITCH_JUMP_PX = 120.0

#: Below this many box-lengths per second, a track is not going anywhere.
#: A rate, not a total displacement: a real vehicle glimpsed for half a second
#: covers little ground in absolute terms but is plainly moving, and a
#: displacement threshold deletes it along with the scenery.
STATIONARY_RATE_PER_S = 0.25


def drop_short_tracks(
    rows: Iterable[dict[str, Any]], min_frames: int
) -> tuple[list[dict[str, Any]], int]:
    """Remove tracks with too few frames. Returns (rows, tracks_dropped).

    A five-frame track carries no usable velocity, and an event referencing one
    is an acceptance-criteria failure rather than a near-miss.
    """
    by_track: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_track[int(row["track_id"])].append(row)

    kept: list[dict[str, Any]] = []
    dropped = 0
    for samples in by_track.values():
        if len(samples) < min_frames:
            dropped += 1
            continue
        kept.extend(samples)
    kept.sort(key=lambda r: (r["frame"], r["track_id"]))
    return kept, dropped


def drop_stationary_tracks(
    rows: Iterable[dict[str, Any]], rate: float = STATIONARY_RATE_PER_S
) -> tuple[list[dict[str, Any]], list[int]]:
    """Remove tracks whose ground-contact point never meaningfully moves.

    Returns ``(rows, dropped_track_ids)``.

    A detector will occasionally lock onto scenery. On the BeamNG clip one of
    six tracks was a patch of treeline held for 75 frames at a standstill; the
    same happens on real footage with parked cars, signage and roadside
    clutter. None of it is traffic and none of it belongs in a conflict stream.

    **Measured as a rate, in box-lengths per second.** Two properties matter
    and an absolute pixel threshold has neither. Size-relative, because a
    vehicle near the camera occupies far more pixels than one at the far end,
    so a fixed pixel budget either misses the near ones or deletes the far
    ones. And per-second, because a real vehicle glimpsed for half a second
    covers little ground in total while plainly moving — an earlier version of
    this function tested total displacement and would have deleted exactly such
    a track on this clip.

    Suppression rule 3 already stops these reaching an event, since a
    standstill is below the moving threshold. This removes them a stage earlier
    so they do not inflate the track count either, which is a number we report.
    """
    by_track: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_track[int(row["track_id"])].append(row)

    kept: list[dict[str, Any]] = []
    dropped: list[int] = []
    for track_id, samples in by_track.items():
        samples.sort(key=lambda r: r["frame"])
        duration = float(samples[-1]["t"]) - float(samples[0]["t"])
        if duration <= 0:
            kept.extend(samples)
            continue
        xs = [(s["bbox"][0] + s["bbox"][2]) / 2.0 for s in samples]
        ys = [s["bbox"][3] for s in samples]
        span = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        size = sum(
            math.hypot(s["bbox"][2] - s["bbox"][0], s["bbox"][3] - s["bbox"][1])
            for s in samples
        ) / len(samples)
        if size <= 0:
            kept.extend(samples)
            continue
        if span / size / duration < rate:
            dropped.append(track_id)
            continue
        kept.extend(samples)
    kept.sort(key=lambda r: (r["frame"], r["track_id"]))
    return kept, sorted(dropped)


@dataclass
class SwitchReport:
    frames: int = 0
    tracks: int = 0
    suspected_switches: int = 0
    switch_details: list[str] = field(default_factory=list)

    @property
    def per_1000_frames(self) -> float:
        return 1000.0 * self.suspected_switches / self.frames if self.frames else 0.0

    def render(self) -> str:
        verdict = "PASS" if self.per_1000_frames < 5.0 else "OVER BUDGET"
        lines = [
            "track hygiene:",
            "  frames                      {}".format(self.frames),
            "  tracks                      {}".format(self.tracks),
            "  suspected identity switches {}".format(self.suspected_switches),
            "  per 1,000 frames            {:.2f}   (budget < 5.00)  {}".format(
                self.per_1000_frames, verdict
            ),
        ]
        lines.extend("  " + d for d in self.switch_details[:10])
        lines.append(
            "  NOTE: this is an automatic screen on positional jumps, not a "
            "substitute for spot-checking 20 tracks against the overlay video. "
            "Report the hand-checked number."
        )
        return "\n".join(lines)


def measure_switch_rate(
    rows: Iterable[dict[str, Any]], jump_px: float = SWITCH_JUMP_PX
) -> SwitchReport:
    """Flag implausible positional jumps within a single track id.

    A screen, not a verdict. It catches the switches that teleport a box across
    the frame, which are the ones that manufacture false severe events. Slow
    swaps between two adjacent vehicles it will miss, which is exactly why the
    acceptance criterion asks for 20 hand-checked tracks as well.
    """
    by_track: dict[int, list[dict[str, Any]]] = defaultdict(list)
    frames: set[int] = set()
    for row in rows:
        by_track[int(row["track_id"])].append(row)
        frames.add(int(row["frame"]))

    report = SwitchReport(frames=len(frames), tracks=len(by_track))
    for track_id, samples in sorted(by_track.items()):
        samples.sort(key=lambda r: r["frame"])
        for prev, cur in zip(samples, samples[1:]):
            if cur["frame"] - prev["frame"] > 3:
                continue  # a gap, not a jump
            pcx = (prev["bbox"][0] + prev["bbox"][2]) / 2.0
            pcy = prev["bbox"][3]
            ccx = (cur["bbox"][0] + cur["bbox"][2]) / 2.0
            ccy = cur["bbox"][3]
            jump = math.hypot(ccx - pcx, ccy - pcy)
            if jump > jump_px:
                report.suspected_switches += 1
                report.switch_details.append(
                    "track {} jumped {:.0f} px at frame {}".format(
                        track_id, jump, cur["frame"]
                    )
                )
    return report
