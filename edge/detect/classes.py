"""COCO to NETRA class mapping. Owner A.

**State this limitation out loud rather than hiding it.**

COCO has no auto-rickshaw class. On Indian roads the auto is everywhere, and an
off-the-shelf YOLO will label it ``car`` or ``truck`` depending on the angle.
That is the most visible weakness of using a stock detector here, and a judge
who knows the domain will spot it inside ten seconds of watching the overlay.

Volunteering it is cheap and buys credibility; being caught hiding it is
expensive. It also has a real consequence downstream: the conflict engine sizes
each vehicle's circle by class (``radii_m`` in config), so an auto detected as
a car is modelled 0.6 m too large, which makes its TTC slightly pessimistic.
Pessimistic in the safe direction, but say so.

We train nothing. YOLOv8n and ByteTrack are used exactly as shipped. Any hour
spent fine-tuning is an hour stolen from the conflict engine, and a fine-tuned
model is harder to defend than an off-the-shelf one.
"""

from __future__ import annotations

#: COCO name -> NETRA class. Anything not listed here is dropped.
COCO_TO_NETRA: dict[str, str] = {
    "car": "car",
    "motorcycle": "motorcycle",
    "truck": "truck",
    "bus": "bus",
    "person": "person",
    "bicycle": "motorcycle",  # two-wheeler kinematics; closest of the six
}

#: The only classes that may appear in tracks_px.jsonl.
NETRA_CLASSES: tuple[str, ...] = ("car", "motorcycle", "truck", "bus", "auto", "person")

#: Recorded so the deck and the overlay can both cite the same sentence.
AUTO_RICKSHAW_LIMITATION = (
    "COCO has no auto-rickshaw class, so autos surface as 'car' or 'truck'. "
    "Stock YOLOv8n, no fine-tuning. Consequence: an auto is modelled with a "
    "car's 2.0 m circle instead of 1.4 m, which biases its TTC slightly "
    "pessimistic."
)


def map_class(coco_name: str) -> str | None:
    """NETRA class for a COCO label, or None if the detection should be dropped."""
    return COCO_TO_NETRA.get(coco_name)


def coco_ids_for(model_names: dict[int, str]) -> list[int]:
    """Class ids to ask the detector for, given its id->name table.

    Filtering at the model rather than after it is worth doing: it is the
    cheapest speed win available, and it costs one line.
    """
    return [i for i, name in model_names.items() if name in COCO_TO_NETRA]
