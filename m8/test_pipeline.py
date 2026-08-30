import json
from pathlib import Path

from pipeline import process_events


BASE_DIR = Path(__file__).resolve().parent.parent
EVENTS_FILE = BASE_DIR / "fixtures" / "m8" / "events.json"


def main():
    with open(EVENTS_FILE, "r") as f:
        events = json.load(f)

    results = process_events(events)

    assert len(results) == len(events)

    for event, result in zip(events, results):
        assert result["event_id"] == event["event_id"]
        assert result["narration"]
        assert len(result["narration"].split()) <= 80
        assert "None" not in result["narration"]

    print("END-TO-END TEST: PASS")
    print(f"Events processed: {len(results)}")
    print("All narrations valid.")


if __name__ == "__main__":
    main()