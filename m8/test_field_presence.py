import json
import re
from pathlib import Path

from integration import process_event


BASE_DIR = Path(__file__).resolve().parent
EVENTS_FILE = BASE_DIR / "test_events_10.json"


def get_source_numbers(event):
    numbers = set()

    if event.get("ttc_s") is not None:
        numbers.add(str(event["ttc_s"]))

    if event.get("pet_s") is not None:
        numbers.add(str(event["pet_s"]))

    vehicle_a = event.get("vehicle_a", {})
    vehicle_b = event.get("vehicle_b", {})

    if vehicle_a.get("speed_kmh") is not None:
        numbers.add(str(vehicle_a["speed_kmh"]))

    if vehicle_b.get("speed_kmh") is not None:
        numbers.add(str(vehicle_b["speed_kmh"]))

    return numbers


def main():
    with open(EVENTS_FILE, "r") as f:
        events = json.load(f)

    assert len(events) == 10, "Expected exactly 10 events"

    for event in events:
        result = process_event(event)
        narration = result["narration"]

        source_numbers = get_source_numbers(event)

        # Remove the timestamp before checking numeric values.
        narration_without_time = narration.replace(
            event["time"], ""
        )

        narration_numbers = re.findall(
            r"\b\d+(?:\.\d+)?\b",
            narration_without_time
        )

        for number in narration_numbers:
            assert number in source_numbers, (
                f"Invented number {number} "
                f"in event {event['event_id']}"
            )

    print("FIELD-PRESENCE TEST: PASS")
    print(f"Events checked: {len(events)}")


if __name__ == "__main__":
    main()