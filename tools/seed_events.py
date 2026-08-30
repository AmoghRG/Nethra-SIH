#!/usr/bin/env python3
"""
POST a realistic spread of ConflictEvents to a running NETRA server.

This is TEST DATA. It is not detector output, and it must not be presented as
a measured result. Its only jobs are to exercise the ingest path and to give
the dashboard enough events that the filters and charts do something visible.

    python3 tools/seed_events.py
    python3 tools/seed_events.py --count 60 --api http://localhost:8000
    python3 tools/seed_events.py --clear-first

Events are spread across the four arms of the junction and across the hours
of a single evening, with severity and detection quality correlated to
conditions the way the real pipeline would produce them:
darker and wetter means lower detection quality (PRD M6).
"""

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

# Keep in sync with web/js/config.js -> junction.center
CENTER = (12.86889, 74.86389)
DEMO_DATE = "2026-08-28"

# Offsets from the junction centre, roughly along each approach.
# ~0.0009 deg latitude is about 100 m.
ARMS = [
    ("north approach",  0.0011,  0.0002),
    ("south approach", -0.0011, -0.0002),
    ("east approach",   0.0002,  0.0013),
    ("west approach",  -0.0003, -0.0013),
]

VEHICLES = ["motorcycle", "motorcycle", "car", "car", "auto", "truck", "bus"]
TYPES = ["crossing conflict", "rear-end conflict", "merging conflict"]

# Busier in the evening peak, which is also when it is wet and dark.
HOUR_WEIGHTS = {
    7: 3, 8: 5, 9: 4, 10: 2, 11: 2, 12: 2, 13: 2, 14: 2,
    15: 2, 16: 3, 17: 5, 18: 7, 19: 8, 20: 6, 21: 4, 22: 2,
}


def weighted_hour(rng):
    hours = list(HOUR_WEIGHTS)
    return rng.choices(hours, weights=[HOUR_WEIGHTS[h] for h in hours])[0]


def build_events(count, seed=None):
    rng = random.Random(seed)
    events = []

    for i in range(count):
        arm_name, dlat, dlng = ARMS[i % len(ARMS)]
        hour = weighted_hour(rng)

        # Sprinkle events along the arm rather than stacking them on one point.
        spread = rng.uniform(0.35, 1.25)
        lat = CENTER[0] + dlat * spread + rng.uniform(-0.00012, 0.00012)
        lng = CENTER[1] + dlng * spread + rng.uniform(-0.00012, 0.00012)

        # TTC skewed toward the near-miss band; severe is < 0.8 s by contract.
        ttc = round(rng.choice([0.42, 0.55, 0.61, 0.68, 0.74, 0.79,
                                0.85, 0.95, 1.05, 1.15, 1.25, 1.35, 1.45]), 2)
        severity = "severe" if ttc < 0.8 else "conflict"

        # Detection quality degrades after dark. The server sets the actual
        # conditions on ingest; this mirrors the same physics so the values
        # are not contradictory.
        dark = hour >= 19 or hour < 6
        dq = 0.90 - (0.16 if dark else 0.0) + rng.uniform(-0.06, 0.06)
        dq = round(max(0.35, min(0.97, dq)), 2)

        va = rng.choice(VEHICLES)
        vb = rng.choice(VEHICLES)

        events.append({
            "event_id": f"evt_seed_{i:04d}",
            "time": f"{DEMO_DATE}T{hour:02d}:{rng.randint(0,59):02d}:{rng.randint(0,59):02d}",
            "location": [round(lat, 6), round(lng, 6)],
            "type": rng.choice(TYPES),
            "ttc_s": ttc,
            "pet_s": round(ttc + rng.uniform(0.2, 0.9), 2),
            "severity": severity,
            "vehicle_a": {"type": va, "speed_kmh": rng.randint(18, 54),
                          "direction": "normal"},
            "vehicle_b": {"type": vb, "speed_kmh": rng.randint(12, 48),
                          "direction": rng.choice(["normal", "normal", "normal",
                                                   "against flow"])},
            "detection_quality": dq,
            # conditions deliberately omitted: it is written only by the
            # server (PRD 5.3). Sending it here would be wrong.
        })

    return events


def post(api, events):
    body = json.dumps(events).encode("utf-8")
    req = urllib.request.Request(
        api.rstrip("/") + "/api/events",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--count", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7,
                    help="RNG seed; same seed gives the same events")
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the events instead of posting them")
    args = ap.parse_args()

    events = build_events(args.count, args.seed)

    if args.dry_run:
        print(json.dumps(events, indent=2))
        return 0

    stored = 0
    for i in range(0, len(events), args.batch):
        chunk = events[i:i + args.batch]
        try:
            resp = post(args.api, chunk)
        except urllib.error.URLError as e:
            print(f"POST failed: {e}", file=sys.stderr)
            print(f"Is the server running at {args.api}?", file=sys.stderr)
            return 1
        if not resp.get("ok"):
            print(f"Server rejected the batch: {resp.get('error')}", file=sys.stderr)
            return 1
        stored += sum(1 for r in resp.get("data", []) if r.get("ok"))
        print(f"  posted {len(chunk)}, stored {stored}/{len(events)}")

    severe = sum(1 for e in events if e["severity"] == "severe")
    print(f"\nSeeded {stored} events ({severe} severe) across "
          f"{len(ARMS)} approaches.")
    if stored < len(events):
        print("Some events were buffered rather than stored — check that "
              "Postgres is reachable. They will replay automatically.")
    print("\nThis is synthetic test data, not detector output. Clear it before "
          "showing any accuracy figures:")
    print("  DELETE FROM events WHERE event_id LIKE 'evt_seed_%';")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
