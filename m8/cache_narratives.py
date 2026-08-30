import json
from pathlib import Path

from integration import process_event


BASE_DIR = Path(__file__).resolve().parent
EVENTS_FILE = BASE_DIR / "test_events_10.json"
CACHE_FILE = BASE_DIR / "narrative_cache.json"


def main():
    with open(EVENTS_FILE, "r") as f:
        events = json.load(f)

    results = []

    for event in events:
        result = process_event(event)

        results.append({
            "event_id": result["event_id"],
            "narration": result["narration"]
        })

    with open(CACHE_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print("NARRATIVE CACHE CREATED")
    print(f"Events cached: {len(results)}")
    print(f"Cache file: {CACHE_FILE}")


if __name__ == "__main__":
    main()