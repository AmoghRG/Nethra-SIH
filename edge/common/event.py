"""The ConflictEvent record: construction, validation, serialisation.

Jointly reachable by A and B; in practice owner B, since events are built in
``edge/conflicts/`` and shipped from ``edge/emit/``.

Three rules from the parent PRD (5.3) are enforced here in code rather than
left to discipline, because each of them is the kind of thing that gets
violated at hour 21 by someone in a hurry:

1. ``severity`` is DERIVED from ``ttc_s``. It is a property with no setter, so
   it cannot be hand-set to make a number look better.
2. ``conditions`` is written only by the server (M6). The edge always emits
   null, and :func:`validate` fails the event if it is not null.
3. There is NO blame or fault field, and none may be added. We describe what
   happened; we do not assign responsibility, because we cannot verify it and
   being wrong harms a real person.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

SEVERE = "severe"
CONFLICT = "conflict"

VALID_CLASSES = ("car", "motorcycle", "truck", "bus", "auto", "person")
VALID_TYPES = ("crossing conflict", "head-on conflict", "rear-end conflict")

#: Parent PRD 5.3 thresholds. Passed in from config at construction so the
#: engine stays configurable, but restated here as the documented defaults.
DEFAULT_TTC_SEVERE_S = 0.8
DEFAULT_TTC_CONFLICT_S = 1.5

#: M7 acceptance criterion: a serialised ConflictEvent is at most 400 bytes.
MAX_EVENT_BYTES = 400


@dataclass
class Vehicle:
    """One party to a conflict.

    **``direction`` is not serialised by the edge at all.** Deciding that a
    vehicle is travelling "against flow" needs C's learned lane directions
    (M5); the edge has no lane map, and we never write a field we do not
    compute ourselves. D adds it on ingest, alongside ``conditions``.

    Emitting it as an explicit null was the first design, and it cost 38 bytes
    of a 400-byte budget - nearly a tenth - to say nothing. The worst-case
    event then serialised at 412 bytes and broke the M7 size criterion. The
    attribute is kept so that a consumer constructing a Vehicle can carry a
    direction; only the edge's own serialisation omits it.
    """

    type: str
    speed_kmh: float
    direction: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "speed_kmh": round(float(self.speed_kmh), 1),
        }


@dataclass
class ConflictEvent:
    """A single vehicle-vehicle conflict, one per encounter.

    Construct via :meth:`build` rather than directly, so that the event id and
    wall-clock stamp are derived consistently.
    """

    event_id: str
    time: str
    location: list[float]
    type: str
    ttc_s: float
    pet_s: float | None
    vehicle_a: Vehicle
    vehicle_b: Vehicle
    detection_quality: float
    track_ids: list[int]
    t_video_s: float
    min_ttc_frame: int
    ttc_severe_s: float = DEFAULT_TTC_SEVERE_S
    ttc_conflict_s: float = DEFAULT_TTC_CONFLICT_S

    #: Written only by the server (M6). Constant on the edge.
    conditions: None = field(default=None, init=False)

    # -- derived -----------------------------------------------------------

    @property
    def severity(self) -> str:
        """Derived, never hand-set. Parent PRD 5.3 hard rule."""
        return SEVERE if self.ttc_s < self.ttc_severe_s else CONFLICT

    # -- construction ------------------------------------------------------

    @staticmethod
    def make_event_id(video_id: str, track_ids: list[int], min_ttc_frame: int) -> str:
        """Deterministic id: ``evt_`` plus 8 hex of a content hash.

        M7 requires ingest to be idempotent on ``event_id``, and the edge
        buffer replays batches blindly after an outage. A counter is only
        unique within one process; a content hash is stable across re-runs,
        restarts and partial replays, which is what idempotency actually
        needs. Track ids are sorted so the pair is unordered.
        """
        pair = sorted(int(t) for t in track_ids)
        key = "{}|{}|{}|{}".format(video_id, pair[0], pair[1], int(min_ttc_frame))
        return "evt_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]

    @staticmethod
    def wall_clock(video_start: str, t_video_s: float) -> str:
        """Wall-clock stamp from the configured video start plus video time.

        ``t`` is seconds from the start of the video; wall-clock timestamps
        are attached only here, at emission. The two are never mixed.
        """
        start = datetime.fromisoformat(video_start)
        return (start + timedelta(seconds=float(t_video_s))).isoformat(
            timespec="seconds"
        )

    @classmethod
    def build(
        cls,
        *,
        video_id: str,
        video_start: str,
        location: list[float],
        conflict_type: str,
        ttc_s: float,
        pet_s: float | None,
        vehicle_a: Vehicle,
        vehicle_b: Vehicle,
        detection_quality: float,
        track_ids: list[int],
        t_video_s: float,
        min_ttc_frame: int,
        ttc_severe_s: float = DEFAULT_TTC_SEVERE_S,
        ttc_conflict_s: float = DEFAULT_TTC_CONFLICT_S,
    ) -> "ConflictEvent":
        return cls(
            event_id=cls.make_event_id(video_id, track_ids, min_ttc_frame),
            time=cls.wall_clock(video_start, t_video_s),
            location=[round(float(location[0]), 6), round(float(location[1]), 6)],
            type=conflict_type,
            ttc_s=round(float(ttc_s), 2),
            pet_s=None if pet_s is None else round(float(pet_s), 2),
            vehicle_a=vehicle_a,
            vehicle_b=vehicle_b,
            detection_quality=round(float(detection_quality), 2),
            track_ids=[int(t) for t in track_ids],
            t_video_s=round(float(t_video_s), 2),
            min_ttc_frame=int(min_ttc_frame),
            ttc_severe_s=ttc_severe_s,
            ttc_conflict_s=ttc_conflict_s,
        )

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Field order matches contracts/conflict_event.schema.json."""
        return {
            "event_id": self.event_id,
            "time": self.time,
            "location": self.location,
            "type": self.type,
            "ttc_s": self.ttc_s,
            "pet_s": self.pet_s,
            "severity": self.severity,
            "vehicle_a": self.vehicle_a.to_dict(),
            "vehicle_b": self.vehicle_b.to_dict(),
            "conditions": self.conditions,
            "detection_quality": self.detection_quality,
            "track_ids": self.track_ids,
            "t_video_s": self.t_video_s,
            "min_ttc_frame": self.min_ttc_frame,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    def byte_size(self) -> int:
        return len(self.to_json().encode("utf-8"))

    # -- validation --------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of contract violations. Empty means the event is good.

        The engine logs and DROPS a failing event; it is never partially
        written. A malformed record downstream is worse than a missing one,
        because it is believed.
        """
        problems: list[str] = []

        if not isinstance(self.event_id, str) or not self.event_id.startswith("evt_"):
            problems.append("event_id must be a string of the form evt_<8 hex>")
        if len(self.event_id) != 12:
            problems.append("event_id must be exactly 12 characters")

        try:
            datetime.fromisoformat(self.time)
        except (TypeError, ValueError):
            problems.append("time is not ISO-8601: " + repr(self.time))

        if len(self.location) != 2:
            problems.append("location must be [lat, lon]")
        elif not (-90 <= self.location[0] <= 90 and -180 <= self.location[1] <= 180):
            problems.append("location out of range: " + repr(self.location))

        if self.type not in VALID_TYPES:
            problems.append("unknown conflict type: " + repr(self.type))

        if not (self.ttc_s >= 0):
            problems.append("ttc_s must be non-negative")
        if self.ttc_s >= self.ttc_conflict_s:
            problems.append(
                "ttc_s {} is not below the conflict threshold {}".format(
                    self.ttc_s, self.ttc_conflict_s
                )
            )
        if self.pet_s is not None and self.pet_s < 0:
            problems.append("pet_s must be non-negative or null")

        if self.severity not in (SEVERE, CONFLICT):
            problems.append("severity must be severe or conflict")

        for name, veh in (("vehicle_a", self.vehicle_a), ("vehicle_b", self.vehicle_b)):
            if veh.type not in VALID_CLASSES:
                problems.append(name + " has unknown class: " + repr(veh.type))
            if veh.speed_kmh < 0:
                problems.append(name + " has negative speed")
            if veh.direction is not None:
                problems.append(
                    name + " direction must be null on the edge; it needs C's lane map"
                )

        if self.conditions is not None:
            problems.append(
                "conditions must be null on the edge; it is written only by the server"
            )

        if not (0.0 <= self.detection_quality <= 1.0):
            problems.append("detection_quality must be in [0, 1]")

        if len(self.track_ids) != 2 or self.track_ids[0] == self.track_ids[1]:
            problems.append("track_ids must be two distinct track ids")

        if self.t_video_s < 0:
            problems.append("t_video_s must be non-negative")
        if self.min_ttc_frame < 0:
            problems.append("min_ttc_frame must be non-negative")

        size = self.byte_size()
        if size > MAX_EVENT_BYTES:
            problems.append(
                "serialised event is {} bytes, over the {} byte limit".format(
                    size, MAX_EVENT_BYTES
                )
            )

        return problems

    def is_valid(self) -> bool:
        return not self.validate()
