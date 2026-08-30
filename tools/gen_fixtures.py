#!/usr/bin/env python3
"""
Regenerate the sample data in fixtures/ and bundle everything into
web/js/fixtures.js (which the standalone build inlines).

Run after changing sample data, or after tools/fetch_roads.py:

    python3 gen_fixtures.py

Road geometry is NOT generated here — it comes from real OpenStreetMap data
via tools/fetch_roads.py. This script only bundles it if present.
"""

import base64
import json
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parent.parent  # repo root
FIX = ROOT / "fixtures"

# Keep in sync with web/js/config.js
JUNCTION = {"name": "Pumpwell Circle, Mangaluru (sample site)", "center": [12.86889, 74.86389]}
CENTER = JUNCTION["center"]

random.seed(42)

VEHICLES = ["motorcycle", "car", "auto", "truck", "bus"]
CONFLICT_TYPES = ["crossing conflict", "rear-end conflict", "merging conflict"]


def conditions_for(hour, rainy):
    if rainy:
        light = "dark" if (hour >= 19 or hour < 6) else "dusk"
        return {"light": light, "weather": "light rain", "surface": "wet"}
    if hour >= 19 or hour < 6:
        light = "dark"
    elif hour in (6, 18):
        light = "dusk"
    else:
        light = "daylight"
    return {"light": light, "weather": "clear", "surface": "dry"}


def build_events(n=18):
    """
    ConflictEvent records per PRD Section 5.3.

    Every fixture event sits on the junction coordinate exactly, because that
    is what the pipeline produces: one clip has one calibration, and one
    calibration has one location. The dashboard groups them into a single
    site with one pin. Scattering them would invent per-event positions the
    system never measures.

    TTC, speeds, conditions and detection quality are the real payload that
    drives the map, filters and charts.
    """
    events = []
    for i in range(n):
        hour = random.choice([7, 8, 8, 9, 13, 14, 17, 18, 18, 19, 20, 21, 22])
        rainy = random.random() < 0.35
        cond = conditions_for(hour, rainy)

        ttc = round(random.choice([0.5, 0.6, 0.65, 0.7, 0.75, 0.8,
                                   0.9, 1.0, 1.1, 1.2, 1.3, 1.4]), 2)
        severity = "severe" if ttc < 0.8 else "conflict"

        # Detection genuinely degrades in rain and darkness — the PRD (M6)
        # calls this out as something to carry and correct for, not hide.
        dq = 0.9
        if cond["weather"] != "clear":
            dq -= 0.18
        if cond["light"] in ("dark", "dusk"):
            dq -= 0.12
        dq = round(max(0.35, dq + random.uniform(-0.05, 0.05)), 2)

        events.append({
            "event_id": f"evt_{404 + i:05d}",
            "time": f"2026-08-28T{hour:02d}:{random.randint(0,59):02d}:{random.randint(0,59):02d}",
            "location": [CENTER[0], CENTER[1]],
            "type": random.choice(CONFLICT_TYPES),
            "ttc_s": ttc,
            "pet_s": round(ttc + random.uniform(0.2, 0.9), 2),
            "severity": severity,
            "vehicle_a": {
                "type": random.choice(VEHICLES),
                "speed_kmh": random.randint(18, 52),
                "direction": "normal",
            },
            "vehicle_b": {
                "type": random.choice(VEHICLES),
                "speed_kmh": random.randint(14, 48),
                "direction": random.choice(["normal", "normal", "against flow"]),
            },
            "conditions": cond,
            "detection_quality": dq,
        })

    # Guarantee the two events that have demo clips attached.
    events[0].update({"event_id": "evt_00417", "ttc_s": 0.8, "severity": "severe",
                      "clip": "clips/evt_00417.webm"})
    events[5].update({"event_id": "evt_00405", "clip": "clips/evt_00405.webm"})
    return events


def build_narratives(events):
    """
    Stands in for GET /api/events/{id}/narrative (M8, owner F).
    Every number here is copied straight from the record — nothing inferred,
    which is the field-presence rule M8 has to satisfy.
    """
    out = {}
    for ev in events:
        if not (ev.get("clip") or ev["severity"] == "severe" or random.random() < 0.4):
            continue
        hh = int(ev["time"][11:13])
        mm = ev["time"][14:16]
        ampm = "am" if hh < 12 else "pm"
        h12 = hh % 12 or 12
        light = ev["conditions"]["light"]
        lead = f"At {h12}:{mm} {ampm}, " + ("after dark" if light == "dark" else
                                            "at dusk" if light == "dusk" else "in daylight")
        if ev["conditions"]["weather"] != "clear":
            lead += f" and in {ev['conditions']['weather']}"
        against = (", which was travelling against the usual flow of traffic"
                   if ev["vehicle_b"]["direction"] == "against flow" else "")
        out[ev["event_id"]] = (
            f"{lead}, a {ev['vehicle_a']['type']} travelling at "
            f"{ev['vehicle_a']['speed_kmh']} km/h came within {ev['ttc_s']} seconds of a "
            f"{ev['vehicle_b']['type']} travelling at {ev['vehicle_b']['speed_kmh']} km/h"
            f"{against}. Recorded as a {ev['severity']} {ev['type']}."
        )
    return out


NORMS = {
    "speed_85_kmh": 52.0,
    "sample_size": 843,
    "lanes": [{"id": 0, "centreline_m": [[0, 0], [40, 2]], "heading_deg": 88}],
    "signal_cycle_s": None,
}

HEALTH = {
    "ok": True,
    "data": {
        "pipeline_status": "idle — no live edge connected",
        "frame_rate_fps": None,
        "escalations_per_min": None,
        "last_ingest": None,
        "note": "Live panel is P2. Wire this up once the edge pipeline (M1-M4) emits.",
    },
    "error": None,
}

CALIBRATION = {
    "video_id": "pumpwell_evening",
    "homography": [[1.02, 0.01, -4120.5], [0.0, 1.05, -6980.2], [0.0, 0.0002, 1.0]],
    "reference_points": [
        {"pixel": [412, 688], "ground_m": [0.0, 0.0], "note": "kerb corner, north approach"},
        {"pixel": [610, 640], "ground_m": [12.4, 0.0], "note": "lane line, east approach"},
        {"pixel": [300, 520], "ground_m": [0.0, 18.0], "note": "footbridge pillar base"},
        {"pixel": [700, 500], "ground_m": [12.4, 18.0], "note": "median edge"},
    ],
    "rms_error_m": 0.34,
    "location": CENTER,
}


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def main():
    events = build_events()
    narratives = build_narratives(events)

    write(FIX / "events.sample.json", events)
    write(FIX / "narratives.json", narratives)
    write(FIX / "norms.json", NORMS)
    write(FIX / "calibration.json", CALIBRATION)
    write(FIX / "api" / "health.json", HEALTH)

    # Clips, inlined so the standalone build plays them with no file access.
    clips = {}
    for clip in sorted((ROOT / "demo" / "clips").glob("*.webm")):
        clips[clip.stem] = base64.b64encode(clip.read_bytes()).decode()

    # Street geometry, kept only so an older build of the dashboard still
    # works. The current one draws no roads and never reads this.
    road_network = None
    rn_path = FIX / "road_network.json"
    if rn_path.exists():
        road_network = json.loads(rn_path.read_text(encoding="utf-8"))

    bundle = {
        "calibration": CALIBRATION,
        "events": events,
        "norms": NORMS,
        "narratives": narratives,
        "health": HEALTH,
        "clips_b64": clips,
        "road_network": road_network,
    }

    js = ROOT / "web" / "js" / "fixtures.js"
    js.parent.mkdir(parents=True, exist_ok=True)
    js.write_text(
        "// Auto-generated by gen_fixtures.py - do not hand-edit.\n"
        "// Mirrors fixtures/*.json (PRD Section 6, fixture-first rule).\n"
        "window.NETRA_FIXTURES = " + json.dumps(bundle) + ";\n",
        encoding="utf-8",
    )

    n_roads = len(road_network["roads"]) if road_network else None
    print(f"events        {len(events)}")
    print(f"narratives    {len(narratives)}")
    print(f"clips         {len(clips)}")
    print(f"road network  {n_roads if n_roads else 'not baked yet - run tools/fetch_roads.py'}")
    print(f"bundle        {js} ({js.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
