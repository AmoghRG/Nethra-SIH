from pathlib import Path
import json
import sys

from flask import Flask, jsonify


BASE_DIR = Path(__file__).resolve().parents[2]

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from m8.incident_writer import write_incident


app = Flask(__name__)

CACHE_FILE = BASE_DIR / "m8" / "narrative_cache.json"
EVENTS_FILE = BASE_DIR / "fixtures" / "m8" / "events.json"


def load_cache():
    if not CACHE_FILE.exists():
        return {}

    with open(CACHE_FILE, "r") as f:
        data = json.load(f)

    return {
        item["event_id"]: item["narration"]
        for item in data
    }


def load_events():
    if not EVENTS_FILE.exists():
        return {}

    with open(EVENTS_FILE, "r") as f:
        data = json.load(f)

    return {
        event["event_id"]: event
        for event in data
    }


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "data": {
            "status": "healthy"
        },
        "error": None
    })


@app.get("/api/events/<event_id>/narrative")
def narrative(event_id):
    cache = load_cache()

    if event_id in cache:
        return jsonify({
            "ok": True,
            "data": {
                "event_id": event_id,
                "narration": cache[event_id]
            },
            "error": None
        })

    events = load_events()

    if event_id not in events:
        return jsonify({
            "ok": False,
            "data": None,
            "error": {
                "code": "EVENT_NOT_FOUND",
                "message": "Event not found"
            }
        }), 404

    narration = write_incident(events[event_id])

    return jsonify({
        "ok": True,
        "data": {
            "event_id": event_id,
            "narration": narration
        },
        "error": None
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)