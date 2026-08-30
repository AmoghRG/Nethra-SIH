import json
from pathlib import Path

from incident_writer import write_incident


BASE_DIR = Path(__file__).resolve().parent.parent
EVENTS_FILE = BASE_DIR / "fixtures" / "m8" / "events.json"


def main():
    with open(EVENTS_FILE, "r") as f:
        events = json.load(f)

    results = []

    for event in events:
        try:
            narration = write_incident(event)

            results.append({
                "event_id": event.get("event_id"),
                "narration": narration
            })

        except Exception as e:
            results.append({
                "event_id": event.get("event_id"),
                "narration": None,
                "error": str(e)
            })

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()