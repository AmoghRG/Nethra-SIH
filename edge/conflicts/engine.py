"""M3 — the conflict engine. Owner B.

``tracks_m.jsonl`` in, validated ConflictEvent records out. Per frame: form
every vehicle pair, apply suppression rules 1-5, compute TTC on the ground
plane, and hand any conflict reading to the debouncer (rule 6). One encounter
produces exactly one event, carrying the minimum TTC observed.

The run summary this produces is not decoration. It is the material for
showing what each suppression rule removed, which is an acceptance criterion,
and it is the answer to "how did you count?".
"""

from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from edge.calibration.homography import Calibration
from edge.common.config import Config
from edge.common.event import ConflictEvent, Vehicle
from edge.common.geometry import (
    decompose_separation,
    heading_difference_deg,
    mps_to_kmh,
)
from edge.conflicts.debounce import Debouncer, Encounter
from edge.conflicts.pet import post_encroachment_time
from edge.conflicts.sample import TrackSample
from edge.conflicts.suppression import RULE_DEBOUNCE, SuppressionEngine
from edge.conflicts.ttc import severity_for, time_to_collision

#: Above this heading difference a pair is meeting head-on rather than crossing.
HEAD_ON_DEG = 160.0


@dataclass
class EngineStats:
    frames: int = 0
    samples_in: int = 0
    tracks_in: int = 0
    tracks_dropped_short: int = 0
    pairs_examined: int = 0
    pairs_suppressed: int = 0
    conflict_readings: int = 0
    suspicious_readings: int = 0
    encounters: int = 0
    events_emitted: int = 0
    events_dropped_invalid: int = 0
    severity_counts: Counter[str] = field(default_factory=Counter)
    drop_reasons: list[str] = field(default_factory=list)

    def render(self, suppression: SuppressionEngine) -> str:
        lines = [
            "conflict engine:",
            "  frames                      {}".format(self.frames),
            "  track-frames in             {}".format(self.samples_in),
            "  tracks in                   {}".format(self.tracks_in),
            "  tracks dropped, too short   {}".format(self.tracks_dropped_short),
            "  pair-frames examined        {}".format(self.pairs_examined),
            "  pair-frames suppressed      {}".format(self.pairs_suppressed),
            "  conflict readings kept      {}".format(self.conflict_readings),
            "  of which suspicious (C<0)   {}".format(self.suspicious_readings),
            "",
            suppression.render(),
            "",
            "  encounters closed           {}".format(self.encounters),
            "  events emitted              {}".format(self.events_emitted),
            "  events dropped, invalid     {}".format(self.events_dropped_invalid),
            "  severe                      {}".format(self.severity_counts["severe"]),
            "  conflict                    {}".format(self.severity_counts["conflict"]),
        ]
        if self.conflict_readings and self.encounters:
            lines.append("")
            lines.append(
                "  debouncing collapsed {} readings into {} events "
                "({:.1f}x). Without it, that first number is what would have "
                "been reported.".format(
                    self.conflict_readings,
                    self.events_emitted,
                    self.conflict_readings / max(self.events_emitted, 1),
                )
            )
        for reason in self.drop_reasons[:10]:
            lines.append("  dropped: " + reason)
        return "\n".join(lines)


class ConflictEngine:
    """Stateful sweep over ground-plane tracks."""

    def __init__(self, cfg: Config, calib: Calibration, trace: bool = False) -> None:
        self.cfg = cfg
        self.calib = calib
        # Opt-in, and off by default. This is presentation and debugging
        # output: it records every pair-frame examined, which is far more than
        # the edge ever needs to carry, and the edge loop must not pay for it
        # in normal operation. With it on, the run summary's per-rule counts
        # can be shown pair by pair rather than only in aggregate.
        self.trace_enabled = trace
        self.trace: list[dict[str, Any]] = []
        self.ttc_severe_s = float(cfg.get("conflicts.ttc_severe_s"))
        self.ttc_conflict_s = float(cfg.get("conflicts.ttc_conflict_s"))
        self.debounce_s = float(cfg.get("conflicts.debounce_s"))
        self.min_track_frames = int(cfg.get("tracker.min_track_frames"))
        self.video_start = str(cfg.get("video.start_time"))

        self.pet_enabled = bool(cfg.get("conflicts.pet_enabled", True))
        self.pet_max_s = float(cfg.get("conflicts.pet_max_s", 10.0))

        self.suppression = SuppressionEngine(cfg, max_range_m=calib.max_range_m)
        self.debouncer = Debouncer(
            self.debounce_s, enabled=bool(cfg.get("suppression." + RULE_DEBOUNCE, True))
        )
        self.stats = EngineStats()
        self._tracks: dict[int, list[TrackSample]] = {}

    # -- main sweep --------------------------------------------------------

    def run(self, rows: Iterable[dict[str, Any]]) -> list[ConflictEvent]:
        """Process a whole track file and return the events it produced.

        Two passes. The sweep collects encounters; events are built afterwards.
        PET is the reason for the split: it is retrospective, and the second
        vehicle often reaches the crossing point *after* the encounter has
        already been closed by the debouncer. Computing it during the sweep
        silently returns null for exactly the crossing conflicts it is meant
        to measure.
        """
        by_frame = self._load(rows)
        encounters: list[Encounter] = []
        last_t = 0.0

        for frame in sorted(by_frame):
            samples = by_frame[frame]
            self.stats.frames += 1
            last_t = samples[0].t

            for a, b in itertools.combinations(samples, 2):
                self.stats.pairs_examined += 1
                self.suppression.observe(a, b)

                reason = self.suppression.check(a, b)
                result = None
                # With tracing on, TTC is computed even for suppressed pairs.
                # The decision below is unaffected - a suppressed pair is still
                # dropped - but the trace then carries the TTC the pair WOULD
                # have produced, which is what makes a before/after suppression
                # comparison a measurement rather than an inference, and what
                # gives the TTC plot a continuous curve instead of one point.
                if reason is None or self.trace_enabled:
                    result = time_to_collision(
                        a.p, a.v, b.p, b.v,
                        self.cfg.radius_m(a.cls), self.cfg.radius_m(b.cls),
                    )
                if self.trace_enabled:
                    self._record(a, b, reason, result)

                if reason is not None:
                    self.stats.pairs_suppressed += 1
                    continue
                if not result.is_finite:
                    continue
                if severity_for(result.ttc_s, self.ttc_severe_s, self.ttc_conflict_s) is None:
                    continue

                self.stats.conflict_readings += 1
                if result.suspicious:
                    self.stats.suspicious_readings += 1
                encounters.extend(
                    self.debouncer.update(a, b, result.ttc_s, result.suspicious)
                )

            encounters.extend(self.debouncer.close_stale(last_t))
            if self.stats.frames % 250 == 0:
                self.suppression.prune(last_t, self.debounce_s * 4)

        encounters.extend(self.debouncer.flush())

        events: list[ConflictEvent] = []
        for enc in encounters:
            events.extend(self._build_event(enc))
        events.sort(key=lambda e: (e.t_video_s, e.event_id))
        return events

    # -- helpers -----------------------------------------------------------

    def _load(self, rows: Iterable[dict[str, Any]]) -> dict[int, list[TrackSample]]:
        """Parse rows, drop under-length tracks, and index by frame.

        Tracks shorter than ``min_track_frames`` are dropped here as well as in
        the projection stage. Belt and braces: an event referencing a
        four-frame track is an acceptance-criteria failure, and this engine can
        be pointed at a hand-made fixture that never went through projection.
        """
        per_track: dict[int, list[TrackSample]] = defaultdict(list)
        for row in rows:
            self.stats.samples_in += 1
            per_track[int(row["track_id"])].append(TrackSample.from_row(row))

        self.stats.tracks_in = len(per_track)
        by_frame: dict[int, list[TrackSample]] = defaultdict(list)
        for track_id, samples in per_track.items():
            if len(samples) < self.min_track_frames:
                self.stats.tracks_dropped_short += 1
                continue
            samples.sort(key=lambda s: s.frame)
            self._tracks[track_id] = samples
            for s in samples:
                by_frame[s.frame].append(s)
        for frame in by_frame:
            by_frame[frame].sort(key=lambda s: s.track_id)
        return by_frame

    def _record(self, a: TrackSample, b: TrackSample, reason, result) -> None:
        """One trace row per pair-frame examined.

        ``ttc_s`` is null when the pair was suppressed (no TTC was computed) or
        when the pair will never come within R, which is the common case. The
        two are distinguished by ``suppressed_by``.
        """
        ttc = None
        if result is not None and result.is_finite:
            ttc = round(float(result.ttc_s), 3)
        self.trace.append({
            "frame": a.frame,
            "t": round(a.t, 3),
            "track_a": min(a.track_id, b.track_id),
            "track_b": max(a.track_id, b.track_id),
            "gap_m": round(float(((a.p - b.p) ** 2).sum() ** 0.5), 2),
            "ttc_s": ttc,
            "case": None if result is None else result.case,
            "suppressed_by": reason,
        })

    def _conflict_type(self, a: TrackSample, b: TrackSample) -> str:
        """Label the encounter from the two headings, where both are usable.

        When the slower vehicle is below walking pace its heading is derived
        from a velocity that is mostly noise, so comparing the two headings
        labels the encounter at random. In that case the geometry answers
        instead: a nearly stationary vehicle lying ahead of a moving one along
        its direction of travel is a rear-end; one off to the side is a
        crossing.
        """
        parallel_deg = float(self.cfg.get("conflicts.parallel_heading_deg"))
        min_moving = float(self.cfg.get("motion.min_moving_speed_mps"))

        if min(a.speed_mps, b.speed_mps) < min_moving:
            fast, slow = (a, b) if a.speed_mps >= b.speed_mps else (b, a)
            longitudinal, lateral = decompose_separation(
                slow.p - fast.p, fast.heading_deg
            )
            return "rear-end conflict" if abs(lateral) <= abs(longitudinal) else "crossing conflict"

        diff = heading_difference_deg(a.heading_deg, b.heading_deg)
        if diff >= HEAD_ON_DEG:
            return "head-on conflict"
        if diff < parallel_deg:
            return "rear-end conflict"
        return "crossing conflict"

    def _pet_for(self, enc: Encounter) -> float | None:
        """PET over the pair's full paths, not just the encounter window.

        Null is a normal answer: two vehicles travelling the same road in the
        same direction never cross, and a crossing further apart in time than
        ``pet_max_s`` is a different encounter rather than this one.
        """
        if not self.pet_enabled:
            return None
        result = post_encroachment_time(
            self._tracks.get(enc.key[0], []),
            self._tracks.get(enc.key[1], []),
            around_t=enc.min_ttc_t,
            max_offset_s=self.pet_max_s,
        )
        if result is None or result.pet_s > self.pet_max_s:
            return None
        return result.pet_s

    def _build_event(self, enc: Encounter) -> list[ConflictEvent]:
        """Turn a closed encounter into at most one validated event."""
        self.stats.encounters += 1
        a, b = enc.a_at_min, enc.b_at_min
        if a is None or b is None:
            return []

        # Canonical ordering: vehicle_a is the lower track id, matching
        # track_ids in the record and the unordered pair key.
        if a.track_id != enc.key[0]:
            a, b = b, a

        event = ConflictEvent.build(
            video_id=self.calib.video_id,
            video_start=self.video_start,
            location=self.calib.location,
            conflict_type=self._conflict_type(a, b),
            ttc_s=enc.min_ttc_s,
            pet_s=self._pet_for(enc),
            vehicle_a=Vehicle(a.cls, mps_to_kmh(a.speed_mps)),
            vehicle_b=Vehicle(b.cls, mps_to_kmh(b.speed_mps)),
            detection_quality=enc.detection_quality,
            track_ids=[a.track_id, b.track_id],
            t_video_s=enc.min_ttc_t,
            min_ttc_frame=enc.min_ttc_frame,
            ttc_severe_s=self.ttc_severe_s,
            ttc_conflict_s=self.ttc_conflict_s,
        )

        problems = event.validate()
        if problems:
            # Logged and dropped, never partially written. A malformed record
            # downstream is worse than a missing one, because it is believed.
            self.stats.events_dropped_invalid += 1
            self.stats.drop_reasons.append(
                "{} tracks {}: {}".format(event.event_id, event.track_ids, "; ".join(problems))
            )
            return []

        self.stats.events_emitted += 1
        self.stats.severity_counts[event.severity] += 1
        return [event]
