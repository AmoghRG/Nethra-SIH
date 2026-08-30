import json
from pathlib import Path

from pipeline import process_events


BASE_DIR = Path(__file__).resolve().parent.parent
EVENTS_FILE = BASE_DIR / "fixtures" / "m8" / "events.json"


with open(EVENTS_FILE, "r") as f:
    events = json.load(f)


results = process_events(events)

print(f"Input events: {len(events)}")
print(f"Output results: {len(results)}")
print()

for result in results:
    print(json.dumps(result, indent=2))
    print("-" * 80)


passed = (
    len(results) == len(events)
    and all(
        result.get("event_id") and result.get("narration")
        for result in results
    )
)

print("PASS" if passed else "FAIL")