"""M3 — false-positive suppression. Owner B. **Read this file twice.**

A naive TTC implementation on Indian urban footage will emit hundreds of
conflicts per minute and every one of them will be garbage. The six rules here
are what stand between the conflict engine and an unusable event stream.

======  ==========================  ==================================================
Rule    Source                      Why it fires
======  ==========================  ==================================================
1       Velocity noise              Detection jitter creates a phantom closing component
2       Lane-splitting two-wheelers Motorcycles filtering between cars are genuinely
                                    close — and that is *normal traffic*, not a near-miss
3       Queued traffic at a signal  Stopped vehicles have tiny separation; C < 0 gives
                                    a meaningless TTC of zero
4       Identity switches           A teleporting track implies an enormous velocity
5       Far-field geometry          Metre error explodes near the horizon
6       No debouncing               At 25 FPS one 2-second encounter emits ~50 events
======  ==========================  ==================================================

**Rule 2 deserves its own paragraph.** Lane discipline on Indian roads is not
what the surrogate-safety literature from Sweden assumes. Motorcycles
filtering between slow cars produce TTC values that look alarming and
represent entirely routine behaviour. Without this rule the event stream is
almost entirely two-wheelers doing something completely normal, and recall
against C's human labels is dismal — because the human labeller marked none of
it. This is the single most important adaptation of the method to the road we
are actually measuring, and it is worth saying out loud in the pitch.

Every rule is individually toggleable under ``suppression:`` in config, so its
effect can be *shown* rather than asserted (edge PRD 6.4).
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, deque
from typing import Deque

import numpy as np

from edge.common.config import Config
from edge.common.geometry import (
    decompose_separation,
    heading_difference_deg,
    kmh_to_mps,
)
from edge.conflicts.sample import TrackSample, pair_key
from edge.conflicts.ttc import closing_speed_mps

RULE_VELOCITY_NOISE = "velocity_noise"
RULE_LANE_SPLITTING = "lane_splitting"
RULE_STOPPED = "stopped_vehicles"
RULE_SPEED_SANITY = "speed_sanity"
RULE_VALIDITY_REGION = "validity_region"
RULE_DEBOUNCE = "debounce"

ALL_RULES = (
    RULE_VELOCITY_NOISE,
    RULE_LANE_SPLITTING,
    RULE_STOPPED,
    RULE_SPEED_SANITY,
    RULE_VALIDITY_REGION,
    RULE_DEBOUNCE,
)


class SuppressionEngine:
    """Applies rules 1-5 to a candidate pair. Rule 6 lives in ``debounce.py``.

    Holds per-pair history, because rule 2 cannot be decided from a single
    frame: a steady lateral gap is only visible over time.
    """

    def __init__(self, cfg: Config, max_range_m: float | None = None) -> None:
        self.cfg = cfg
        self.enabled = {rule: bool(cfg.get("suppression." + rule, True)) for rule in ALL_RULES}

        self.min_closing_mps = float(cfg.get("conflicts.min_closing_speed_mps"))
        self.parallel_deg = float(cfg.get("conflicts.parallel_heading_deg"))
        self.min_moving_mps = float(cfg.get("motion.min_moving_speed_mps"))
        self.max_speed_mps = kmh_to_mps(float(cfg.get("geometry.max_speed_kmh")))

        self.lateral_stability_m = float(cfg.get("lane_split.lateral_stability_m"))
        self.min_lateral_offset_m = float(cfg.get("lane_split.min_lateral_offset_m"))
        self.history_frames = int(cfg.get("lane_split.history_frames"))

        self.max_range_m = max_range_m
        self.counts: Counter[str] = Counter()
        self._lateral: dict[tuple[int, int], Deque[float]] = {}
        self._last_seen: dict[tuple[int, int], float] = {}

    # -- per-pair history --------------------------------------------------

    @staticmethod
    def _reference_heading(a: TrackSample, b: TrackSample) -> float:
        """Direction to decompose separation along: the FASTER vehicle's.

        Heading is derived from a velocity vector, so at low speed it is noise.
        A vehicle crawling at 2 km/h in dense traffic produced headings ranging
        over 50 degrees between consecutive frames on the demo clip. Taking the
        reference from whichever vehicle is moving faster makes the lateral and
        longitudinal split stable, which is what rule 2 depends on.
        """
        return a.heading_deg if a.speed_mps >= b.speed_mps else b.heading_deg

    def observe(self, a: TrackSample, b: TrackSample) -> None:
        """Record this frame's lateral gap for the pair.

        Called for every pair examined, whatever else happens to it, so that
        rule 2 has history to work from the moment a pair becomes a candidate.
        """
        key = pair_key(a, b)
        dp = a.p - b.p
        _, lateral = decompose_separation(dp, self._reference_heading(a, b))
        hist = self._lateral.get(key)
        if hist is None:
            hist = deque(maxlen=max(self.history_frames * 3, 6))
            self._lateral[key] = hist
        hist.append((a.t, abs(lateral)))
        self._last_seen[key] = a.t

    def prune(self, now_t: float, older_than_s: float) -> None:
        """Forget pairs not seen recently, so history does not grow forever."""
        stale = [k for k, t in self._last_seen.items() if now_t - t > older_than_s]
        for key in stale:
            self._lateral.pop(key, None)
            self._last_seen.pop(key, None)

    # -- the rules ---------------------------------------------------------

    def check(self, a: TrackSample, b: TrackSample) -> str | None:
        """Return the name of the rule that suppresses this pair, or None.

        Ordered cheapest-first, and by how decisively each rule disqualifies a
        pair. The returned reason is counted so the run summary can show what
        each rule actually removed.
        """
        for rule, fired in (
            (RULE_SPEED_SANITY, self._speed_sanity(a, b)),
            (RULE_VALIDITY_REGION, self._out_of_range(a, b)),
            (RULE_STOPPED, self._stopped(a, b)),
            (RULE_VELOCITY_NOISE, self._velocity_noise(a, b)),
            (RULE_LANE_SPLITTING, self._lane_splitting(a, b)),
        ):
            if self.enabled[rule] and fired:
                self.counts[rule] += 1
                return rule
        return None

    def _speed_sanity(self, a: TrackSample, b: TrackSample) -> bool:
        """Rule 4. An implausible speed is an identity switch, not a vehicle.

        When track 87 switches to a vehicle 15 metres away, the smoothed
        velocity registers an enormous jump and the engine sees something
        apparently doing 200 km/h at another vehicle. One ID switch can
        manufacture one false *severe* event, and severe events are the
        headline number.
        """
        return max(a.speed_mps, b.speed_mps) > self.max_speed_mps

    def _out_of_range(self, a: TrackSample, b: TrackSample) -> bool:
        """Rule 5. Near the vanishing point a one-pixel error is tens of metres."""
        if self.max_range_m is None:
            return False
        return max(a.range_m, b.range_m) > self.max_range_m

    def _stopped(self, a: TrackSample, b: TrackSample) -> bool:
        """Rule 3. Suppress stationary queued traffic, but preserve real collisions.

        If both vehicles are stopped below min_moving_mps, it is a stationary queue.
        If at least one vehicle is moving with closing speed towards the other (e.g.
        rear-ending a stopped car at a signal or pedestrian), it is an active hazard.
        """
        # If both are stopped and not in physical impact, it is a stationary queue
        if max(a.speed_mps, b.speed_mps) < self.min_moving_mps:
            return True
        return False

    def _velocity_noise(self, a: TrackSample, b: TrackSample) -> bool:
        """Rule 1. Require a real closing speed, not a jitter artefact.

        Closing speed (the component along the line joining the two vehicles)
        rather than raw ``|dv|``: two vehicles running side by side at very
        different speeds have a large ``|dv|`` and are not approaching at all.
        """
        return closing_speed_mps(a.p, a.v, b.p, b.v) < self.min_closing_mps

    def _lane_splitting(self, a: TrackSample, b: TrackSample) -> bool:
        """Rule 2. A two-wheeler filtering between cars is normal traffic.

        Three conditions must hold together:

        1. **Parallel.** Headings agree within ``parallel_heading_deg``.
        2. **Steady lateral gap.** Over the recent window the sideways
           separation has a standard deviation below ``lateral_stability_m``.
           A vehicle genuinely cutting across shows a collapsing gap and fails
           this.
        3. **Offset, not in line.** The lateral gap is at least
           ``min_lateral_offset_m``, so the two are travelling on parallel
           paths rather than one behind the other.

        Condition 3 is the one that is easy to omit and expensive to omit.
        Without it, a car following directly behind another is also "parallel
        with a steady lateral gap", so every genuine rear-end conflict would be
        suppressed along with the filtering motorcycles. The lateral gap is
        what tells the two apart: a follower shares the path (gap ~0), a
        filterer runs beside it (gap ~1.5 m).

        Note that the rule deliberately does **not** test longitudinal
        distance. A motorcycle closing on the gap between two cars from 15 m
        back is still filtering, and the circle model will report an alarming
        TTC for it the whole way in, because the circles are far fatter than a
        motorcycle straddling a lane line. What makes it harmless is that the
        lateral gap never closes — which is exactly what conditions 2 and 3
        test, at any following distance.

        Condition 2 measures spread, not trend. Fitting a closing *rate*
        instead was tried on the demo clip and was four times worse: 34 events
        against 8. The road bends, so the reference heading rotates, and a
        least-squares slope over half a second picks that rotation up as 1 to
        2 m/s of apparent convergence even while the gap is plainly steady.
        Spread over the same window stays well inside the threshold. The
        variance test is also insensitive to its own threshold here - 0.75,
        1.5 and 3.0 m all give the same eight events - so it is not a knife
        edge that happens to sit in the right place.

        **Condition 1 is skipped when the slower vehicle is barely moving.**
        Its heading is computed from its velocity, so below walking pace it is
        not a measurement at all. On the demo clip this was the single largest
        source of false severe events: a car crawling in congestion showed
        headings scattered over 50 degrees, the parallelism test failed on that
        noise, and a car passing cleanly in the *adjacent lane* was reported as
        a severe conflict. Two cars are modelled as circles of radius 2.0 m
        each, so on a 3.5 m lane they always overlap laterally; without rule 2
        every overtake in the next lane becomes a near-miss. Testing
        parallelism against a direction that cannot be measured is worse than
        not testing it, because it fails open.
        """
        hist = self._lateral.get(pair_key(a, b))
        if not hist:
            return False

        both_measurable = min(a.speed_mps, b.speed_mps) >= self.min_moving_mps
        if both_measurable and heading_difference_deg(
            a.heading_deg, b.heading_deg
        ) >= self.parallel_deg:
            return False

        laterals = [v for _, v in hist]
        if statistics.fmean(laterals) < self.min_lateral_offset_m:
            return False

        # Not enough history to judge steadiness yet. Suppress anyway rather
        # than waiting: a parallel pair already a full lane apart is
        # adjacent-lane traffic on the evidence of a single frame, and the
        # window only refines that. Waiting is what leaked on the demo clip.
        # The debouncer opens an encounter on the first frame a pair is seen
        # and locks in its minimum TTC, so a rule that cannot fire until frame
        # five never gets to speak. Removing that wait alone took the clip from
        # 34 events to 8.
        if len(hist) < self.history_frames:
            return True
        return statistics.pstdev(laterals) < self.lateral_stability_m

    # -- reporting ---------------------------------------------------------

    def render(self) -> str:
        lines = ["suppression (pair-frames removed by each rule):"]
        for rule in ALL_RULES:
            if rule == RULE_DEBOUNCE:
                continue
            state = "on " if self.enabled[rule] else "OFF"
            lines.append("  [{}] {:<18} {}".format(state, rule, self.counts[rule]))
        return "\n".join(lines)
