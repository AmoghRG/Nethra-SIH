import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
CACHE_FILE = BASE_DIR / "narrative_cache.json"


def main():
    with open(CACHE_FILE, "r") as f:
        narratives = json.load(f)

    print("=" * 80)
    print("NETHRA — INCIDENT NARRATION DEMO")
    print("=" * 80)
    print()

    print(f"Loaded {len(narratives)} cached incident narrations.")
    print("Mode: OFFLINE / CACHED")
    print()

    for item in narratives:
        print(f"Event: {item['event_id']}")
        print(item["narration"])
        print("-" * 80)


if __name__ == "__main__":
    main()