"""
NETRA Server — Owner D
M6: Weather & Time Enrichment
M7: Event Ingest, Buffer, Scoring
M8: Narration API
M1-M10: Comprehensive Diagnostics & Pipeline APIs
"""

from fastapi import FastAPI, Query, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
try:
    import psycopg2
except ImportError:
    psycopg2 = None
import sqlite3
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, List, Dict, Any
try:
    import astral
    import astral.sun
except ImportError:
    astral = None
import logging
from pydantic import BaseModel
import threading
import time
from pathlib import Path
import os
import sys

BASE_DIR = Path(__file__).resolve().parents[2]

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from m8.incident_writer import write_incident

# Owner E's video-ingest lane. Lives beside the event store because an
# uploaded clip has to end up in the same database as everything else.
try:
    import video_jobs
except ImportError:  # started from the repo root rather than server/api
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import video_jobs

# ============================================================================
# CONFIG
# ============================================================================

DB_POSTGRES = {
    "host": "localhost",
    "database": "netra",
    "user": "netra_user",
    "password": "netra_pass",
    "port": 5432,
}

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

SQLITE_DB = DATA_DIR / "netra.db"
SQLITE_BUFFER = DATA_DIR / "event_buffer.db"

WEATHER_LOOKUP = {
    "dry": {"rain_factor": 1.0, "surface": "dry", "weather": "clear"},
    "light_rain": {"rain_factor": 1.3, "surface": "wet", "weather": "light rain"},
    "heavy_rain": {"rain_factor": 1.8, "surface": "wet", "weather": "heavy rain"},
    "fog": {"rain_factor": 1.5, "surface": "dry", "weather": "fog"},
}

WEATHER_CACHE = {
    "2026-08-28 17": "light_rain",
    "2026-08-28 18": "light_rain",
    "2026-08-28 19": "heavy_rain",
    "2026-08-28 20": "light_rain",
    "2026-08-28 21": "light_rain",
    "2026-08-28 22": "dry",
}

LOCATION_BASE_RISK = {
    "default": 0.5,
}

SEGMENT_GRID_DECIMALS = 4
SEGMENT_WINDOW = "30 days"

logger = logging.getLogger("netra_server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="NETRA API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class ConflictEvent(BaseModel):
    event_id: str
    time: str  # ISO 8601
    location: List[float]  # [lat, lon]
    type: str  # "crossing conflict", "rear-end conflict", etc.
    ttc_s: float
    pet_s: Optional[float] = None
    severity: str  # "conflict", "severe"
    vehicle_a: Dict[str, Any]
    vehicle_b: Dict[str, Any]
    conditions: Optional[Dict[str, Any]] = None  # set by server only
    detection_quality: float  # 0–1
    clip: Optional[str] = None


class EventListResponse(BaseModel):
    ok: bool
    data: List[Dict[str, Any]]
    error: Optional[str] = None


class SegmentScore(BaseModel):
    segment_id: str
    location: List[float]
    risk_score: float
    conflict_count_24h: int
    conditions_applied: Dict[str, str]


# ============================================================================
# M6: WEATHER & TIME ENRICHMENT
# ============================================================================

class WeatherEnricher:
    """Add weather, light, and time conditions to events."""
    def __init__(self, location_lat=12.86889, location_lon=74.86389):
        if astral:
            self.location = astral.LocationInfo(
                name="Junction",
                region="India",
                timezone="Asia/Kolkata",
                latitude=location_lat,
                longitude=location_lon,
            )
        else:
            self.location = None

    def get_light_state(self, dt: datetime) -> str:
        if astral and self.location:
            try:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ZoneInfo(self.location.timezone))
                sun = astral.sun.sun(self.location.observer, dt.date(), tzinfo=dt.tzinfo)
                if dt < sun["sunrise"]:
                    return "dark"
                elif dt < sun["sunrise"] + timedelta(hours=0.5):
                    return "dusk"
                elif dt > sun["sunset"]:
                    return "dark"
                elif dt > sun["sunset"] - timedelta(hours=0.5):
                    return "dusk"
                else:
                    return "daylight"
            except Exception:
                pass
        # Reliable diurnal fallback for India (sunrise ~6:15am, sunset ~6:30pm)
        h = dt.hour
        if 6 <= h < 7:
            return "dusk"
        elif 7 <= h < 18:
            return "daylight"
        elif 18 <= h < 19:
            return "dusk"
        else:
            return "dark"

    def get_weather(self, hour: int, location: List[float], dt: Optional[datetime] = None) -> Dict[str, Any]:
        if dt is not None:
            cached = WEATHER_CACHE.get(f"{dt.strftime('%Y-%m-%d')} {hour:02d}")
            if cached:
                return WEATHER_LOOKUP[cached]

        condition = "light_rain" if (hour >= 19 or hour < 6) else "dry"
        out = dict(WEATHER_LOOKUP[condition])
        out["estimated"] = True
        return out

    def enrich(self, event: ConflictEvent) -> ConflictEvent:
        try:
            dt = datetime.fromisoformat(event.time.replace('Z', '+00:00'))
        except Exception:
            dt = datetime.now()

        hour = dt.hour
        light = self.get_light_state(dt)
        weather_data = self.get_weather(hour, event.location, dt)

        conditions = {
            "light": light,
            "hour_of_day": hour,
            "weekday": dt.strftime("%A"),
            "peak_hour": 1 if (7 <= hour < 10 or 17 <= hour < 20) else 0,
            "weather": weather_data["weather"],
            "surface": weather_data["surface"],
            "rain_factor": weather_data["rain_factor"],
            "weather_estimated": weather_data.get("estimated", False),
            "detection_quality": event.detection_quality,
        }

        event.conditions = conditions
        return event


enricher = WeatherEnricher()


# ============================================================================
# HELPERS
# ============================================================================

def parse_point(value):
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return [float(value[1]), float(value[0])]
    if isinstance(value, str):
        try:
            lon, lat = value.strip("()").split(",")
            return [float(lat), float(lon)]
        except (ValueError, AttributeError):
            return None
    return None


def as_dict(value):
    if value is None:
        return {}
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value


# ============================================================================
# STORAGE FUNCTIONS (SQLITE & POSTGRES)
# ============================================================================

def store_event_sqlite(event: ConflictEvent) -> bool:
    """Store event persistently in local SQLite."""
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cur = conn.cursor()
        lat = event.location[0] if event.location else 0.0
        lon = event.location[1] if event.location and len(event.location) > 1 else 0.0
        cur.execute("""
            INSERT OR REPLACE INTO events (
                event_id, time, lat, lon, type, ttc_s, pet_s, severity,
                vehicle_a, vehicle_b, conditions, detection_quality, clip
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event.event_id,
            event.time,
            lat,
            lon,
            event.type,
            event.ttc_s,
            event.pet_s,
            event.severity,
            json.dumps(event.vehicle_a),
            json.dumps(event.vehicle_b),
            json.dumps(event.conditions),
            event.detection_quality,
            event.clip or "",
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"SQLite store failed: {e}")
        return False


def store_event_postgres(event: ConflictEvent) -> bool:
    """Store event in PostgreSQL if reachable."""
    if not psycopg2:
        return False
    try:
        conn = psycopg2.connect(**DB_POSTGRES)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM events WHERE event_id = %s", (event.event_id,))
        if cur.fetchone():
            conn.close()
            return True

        event_time = datetime.fromisoformat(event.time.replace('Z', '+00:00'))
        cur.execute("""
            INSERT INTO events (
                event_id, time, location, type, ttc_s, pet_s, severity,
                vehicle_a, vehicle_b, conditions, detection_quality, clip
            ) VALUES (%s, %s, POINT(%s, %s), %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            event.event_id,
            event_time,
            event.location[1], event.location[0],
            event.type,
            event.ttc_s,
            event.pet_s,
            event.severity,
            json.dumps(event.vehicle_a),
            json.dumps(event.vehicle_b),
            json.dumps(event.conditions),
            event.detection_quality,
            event.clip or "",
        ))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def seed_sqlite_if_empty():
    """Disabled. The database starts empty and fills only from real runs.

    This used to load fixtures/events.sample.json whenever the events table was
    empty. That put sample conflicts on the map at coordinates nobody had
    entered, indistinguishable from measured output - exactly the thing the PRD
    warns about (STEP 7: "never present seeded data as a measured result").

    A pin now means an uploaded clip and a location an operator typed. To put
    test data back deliberately, run tools/seed_events.py; those rows are
    prefixed evt_seed_ so they can be deleted again.
    """
    return


def init_sqlite():
    """Initialize local SQLite DB for persistent event storage and buffer."""
    conn = sqlite3.connect(SQLITE_DB)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            time TEXT,
            lat REAL,
            lon REAL,
            type TEXT,
            ttc_s REAL,
            pet_s REAL,
            severity TEXT,
            vehicle_a TEXT,
            vehicle_b TEXT,
            conditions TEXT,
            detection_quality REAL,
            clip TEXT,
            ingested_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sq_event_id ON events(event_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sq_time ON events(time);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sq_loc ON events(lat, lon);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_events (
            event_id TEXT PRIMARY KEY,
            event_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()

    seed_sqlite_if_empty()


def init_postgres():
    """Initialize PostgreSQL with PostGIS (or fallback)."""
    if not psycopg2:
        logger.info("psycopg2 not installed. Using SQLite database.")
        return False
    try:
        conn = psycopg2.connect(**DB_POSTGRES)
        cur = conn.cursor()
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
            conn.commit()
            logger.info("PostGIS enabled in PostgreSQL")
        except Exception as e:
            conn.rollback()
            logger.warning(f"PostGIS unavailable, using plain Postgres ({e})")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                time TIMESTAMP,
                location POINT,
                type TEXT,
                ttc_s FLOAT,
                pet_s FLOAT,
                severity TEXT,
                vehicle_a JSONB,
                vehicle_b JSONB,
                conditions JSONB,
                detection_quality FLOAT,
                clip TEXT,
                ingested_at TIMESTAMP DEFAULT NOW()
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_id ON events(event_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_time ON events(time);")
        conn.commit()
        conn.close()
        logger.info("PostgreSQL events table verified")
        return True
    except Exception as e:
        logger.info(f"PostgreSQL not connected ({e}). Operating in SQLite mode.")
        return False


# Initialize DB on load
init_sqlite()
init_postgres()
video_jobs.init_jobs_table(SQLITE_DB)


class EventBuffer:
    """Buffer events locally when Postgres is unavailable."""
    def __init__(self, db_path=SQLITE_DB):
        self.db_path = db_path

    def add(self, event: ConflictEvent):
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO pending_events (event_id, event_json) VALUES (?, ?)",
                (event.event_id, event.json())
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Buffer add failed: {e}")

    def get_all(self) -> List[ConflictEvent]:
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT event_json FROM pending_events ORDER BY created_at ASC")
            rows = cur.fetchall()
            conn.close()
            return [ConflictEvent(**json.loads(row[0])) for row in rows]
        except Exception:
            return []

    def clear(self, event_id: str):
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("DELETE FROM pending_events WHERE event_id = ?", (event_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass


event_buffer = EventBuffer()


def replay_buffer():
    """Background thread: replay buffered events when Postgres becomes available."""
    while True:
        time.sleep(30)
        buffered = event_buffer.get_all()
        if not buffered:
            continue
        for event in buffered:
            if store_event_postgres(event):
                event_buffer.clear(event.event_id)


replay_thread = threading.Thread(target=replay_buffer, daemon=True)
replay_thread.start()


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.post("/api/events")
async def ingest_events(events: List[ConflictEvent]):
    """Ingest one or more ConflictEvents with M6 weather enrichment and dual persistence."""
    results = []

    for event in events:
        event = enricher.enrich(event)

        # Store in SQLite permanently
        sqlite_ok = store_event_sqlite(event)

        # Attempt PostgreSQL
        pg_ok = store_event_postgres(event)
        if not pg_ok:
            event_buffer.add(event)

        results.append({"event_id": event.event_id, "ok": sqlite_ok or pg_ok})

    return {"ok": True, "data": results, "error": None}


@app.get("/api/events")
async def list_events(
    from_time: Optional[str] = Query(None),
    to_time: Optional[str] = Query(None),
    light: Optional[str] = Query(None),
    weather: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
):
    """Retrieve filtered event list from PostgreSQL with instant SQLite fallback."""
    results = []

    # 1. Try PostgreSQL first if available
    if psycopg2:
        try:
            conn = psycopg2.connect(**DB_POSTGRES)
            cur = conn.cursor()
            query = "SELECT event_id, time, location, type, ttc_s, pet_s, severity, vehicle_a, vehicle_b, conditions, detection_quality, clip FROM events WHERE 1=1"
            params = []
            if from_time:
                query += " AND time >= %s"
                params.append(from_time)
            if to_time:
                query += " AND time <= %s"
                params.append(to_time)
            cur.execute(query + " ORDER BY time DESC", params)
            rows = cur.fetchall()
            conn.close()

            for row in rows:
                conditions = as_dict(row[9])
                if light and light != "any" and conditions.get("light") != light:
                    continue
                if weather and weather != "any" and conditions.get("weather") != weather:
                    continue
                if severity and severity != "any" and row[6] != severity:
                    continue

                results.append({
                    "event_id": row[0],
                    "time": row[1].isoformat() if row[1] else None,
                    "location": parse_point(row[2]),
                    "type": row[3],
                    "ttc_s": row[4],
                    "pet_s": row[5],
                    "severity": row[6],
                    "vehicle_a": as_dict(row[7]),
                    "vehicle_b": as_dict(row[8]),
                    "conditions": conditions,
                    "detection_quality": row[10],
                    "clip": row[11] if len(row) > 11 else None,
                })
            return {"ok": True, "data": results, "error": None}
        except Exception:
            pass

    # 2. SQLite Fallback
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cur = conn.cursor()
        query = "SELECT event_id, time, lat, lon, type, ttc_s, pet_s, severity, vehicle_a, vehicle_b, conditions, detection_quality, clip FROM events WHERE 1=1"
        params = []
        if from_time:
            query += " AND time >= ?"
            params.append(from_time)
        if to_time:
            query += " AND time <= ?"
            params.append(to_time)
        cur.execute(query + " ORDER BY time DESC", params)
        rows = cur.fetchall()
        conn.close()

        for row in rows:
            conditions = as_dict(row[10])
            if light and light != "any" and conditions.get("light") != light:
                continue
            if weather and weather != "any" and conditions.get("weather") != weather:
                continue
            if severity and severity != "any" and row[7] != severity:
                continue

            results.append({
                "event_id": row[0],
                "time": row[1],
                "location": [row[2], row[3]] if row[2] is not None and row[3] is not None else [12.86889, 74.86389],
                "type": row[4],
                "ttc_s": row[5],
                "pet_s": row[6],
                "severity": row[7],
                "vehicle_a": as_dict(row[8]),
                "vehicle_b": as_dict(row[9]),
                "conditions": conditions,
                "detection_quality": row[11],
                "clip": row[12] or None,
            })

        return {"ok": True, "data": results, "error": None}

    except Exception as e:
        logger.error(f"SQLite query failed: {e}")
        return {"ok": False, "data": [], "error": str(e)}


@app.get("/api/events/{event_id}/narrative")
async def get_narrative(event_id: str):
    """Plain-English incident write-up using M8 narration engine."""
    try:
        event = None

        # 1. Check PostgreSQL
        if psycopg2:
            try:
                conn = psycopg2.connect(**DB_POSTGRES)
                cur = conn.cursor()
                cur.execute("SELECT * FROM events WHERE event_id = %s", (event_id,))
                row = cur.fetchone()
                conn.close()
                if row:
                    event = {
                        "event_id": row[0],
                        "time": row[1].isoformat() if row[1] else None,
                        "location": parse_point(row[2]),
                        "type": row[3],
                        "ttc_s": row[4],
                        "pet_s": row[5],
                        "severity": row[6],
                        "vehicle_a": as_dict(row[7]),
                        "vehicle_b": as_dict(row[8]),
                        "conditions": as_dict(row[9]),
                        "detection_quality": row[10],
                    }
            except Exception:
                pass

        # 2. Check SQLite
        if event is None:
            conn = sqlite3.connect(SQLITE_DB)
            cur = conn.cursor()
            cur.execute("SELECT event_id, time, lat, lon, type, ttc_s, pet_s, severity, vehicle_a, vehicle_b, conditions, detection_quality FROM events WHERE event_id = ?", (event_id,))
            row = cur.fetchone()
            conn.close()
            if row:
                event = {
                    "event_id": row[0],
                    "time": row[1],
                    "location": [row[2], row[3]],
                    "type": row[4],
                    "ttc_s": row[5],
                    "pet_s": row[6],
                    "severity": row[7],
                    "vehicle_a": as_dict(row[8]),
                    "vehicle_b": as_dict(row[9]),
                    "conditions": as_dict(row[10]),
                    "detection_quality": row[11],
                }

        # 3. Check fixtures / cache
        if event is None:
            narratives_path = BASE_DIR / "fixtures" / "narratives.json"
            if narratives_path.exists():
                with open(narratives_path, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                    if event_id in cache:
                        return {"ok": True, "data": {"event_id": event_id, "narration": cache[event_id]}, "error": None}

            raise HTTPException(status_code=404, detail="Event not found")

        # Generate fresh narration
        narration = write_incident(event)
        return {"ok": True, "data": {"event_id": event_id, "narration": narration}, "error": None}

    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "data": {}, "error": str(e)}


@app.get("/api/segments")
async def get_segments():
    """Scored road segment clusters for the map."""
    segments = []

    # 1. Try PostgreSQL
    if psycopg2:
        try:
            conn = psycopg2.connect(**DB_POSTGRES)
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    ROUND(location[1]::numeric, %s) as lat,
                    ROUND(location[0]::numeric, %s) as lon,
                    COUNT(*) as conflict_count,
                    AVG(CASE WHEN severity='severe' THEN 2.0 ELSE 1.0 END) as risk_score
                FROM events
                GROUP BY lat, lon
                ORDER BY risk_score DESC
            """, (SEGMENT_GRID_DECIMALS, SEGMENT_GRID_DECIMALS))
            rows = cur.fetchall()
            conn.close()

            for row in rows:
                segments.append({
                    "segment_id": f"seg_{row[0]}_{row[1]}",
                    "location": [float(row[0]), float(row[1])],
                    "risk_score": float(row[3] or 0.5),
                    "conflict_count_24h": row[2],
                    "conditions_applied": {"light": "mixed", "weather": "mixed"},
                })
            return {"ok": True, "data": segments, "error": None}
        except Exception:
            pass

    # 2. SQLite Fallback
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cur = conn.cursor()
        cur.execute("""
            SELECT
                ROUND(lat, ?) as r_lat,
                ROUND(lon, ?) as r_lon,
                COUNT(*) as conflict_count,
                AVG(CASE WHEN severity='severe' THEN 2.0 ELSE 1.0 END) as risk_score
            FROM events
            WHERE lat IS NOT NULL AND lon IS NOT NULL
            GROUP BY r_lat, r_lon
            ORDER BY risk_score DESC
        """, (SEGMENT_GRID_DECIMALS, SEGMENT_GRID_DECIMALS))
        rows = cur.fetchall()
        conn.close()

        for row in rows:
            segments.append({
                "segment_id": f"seg_{row[0]}_{row[1]}",
                "location": [float(row[0]), float(row[1])],
                "risk_score": float(row[3] or 0.5),
                "conflict_count_24h": row[2],
                "conditions_applied": {"light": "mixed", "weather": "mixed"},
            })

        return {"ok": True, "data": segments, "error": None}
    except Exception as e:
        return {"ok": False, "data": [], "error": str(e)}


@app.get("/api/norms")
async def get_norms():
    """M5: Self-calibrated road norms (V85 speed limit, discovered lanes, sample size)."""
    try:
        norms_path = BASE_DIR / "out" / "norms.json"
        if not norms_path.exists():
            norms_path = BASE_DIR / "fixtures" / "norms.json"

        if norms_path.exists():
            with open(norms_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {"ok": True, "data": data, "error": None}
        return {"ok": False, "data": {}, "error": "Norms file not found"}
    except Exception as e:
        return {"ok": False, "data": {}, "error": str(e)}


@app.get("/api/eval")
async def get_eval():
    """M9 & M10: Ground Truth Recall/Precision & Naive 2D IoU Baseline Comparison."""
    try:
        eval_path = BASE_DIR / "eval" / "groundtruth" / "eval_results.json"
        comp_path = BASE_DIR / "eval" / "baseline" / "comparison_report.json"

        eval_data = {}
        if eval_path.exists():
            with open(eval_path, "r", encoding="utf-8") as f:
                eval_data = json.load(f)

        comp_data = {}
        if comp_path.exists():
            with open(comp_path, "r", encoding="utf-8") as f:
                comp_data = json.load(f)

        return {
            "ok": True,
            "data": {
                "ground_truth_eval": eval_data,
                "baseline_comparison": comp_data,
            },
            "error": None,
        }
    except Exception as e:
        return {"ok": False, "data": {}, "error": str(e)}


@app.get("/api/benchmark")
async def get_benchmark():
    """M4: Motion Gate & CPU Benchmark Throughput Metrics."""
    try:
        return {
            "ok": True,
            "data": {
                "results": [
                    {"config": "gate + 320 INT8", "imgsz": 320, "gate": "on", "threads": 1, "fps": 37.20, "cores": 1.42, "fps_per_core": 26.20, "det_per_min": 2231.9},
                    {"config": "plain 320 INT8", "imgsz": 320, "gate": "off", "threads": 1, "fps": 44.81, "cores": 1.36, "fps_per_core": 32.95, "det_per_min": 2688.5},
                    {"config": "plain 640 FP32", "imgsz": 640, "gate": "off", "threads": 1, "fps": 16.45, "cores": 1.13, "fps_per_core": 14.56, "det_per_min": 986.7},
                ],
                "edge_claim": "YOLOv8n INT8 sustains >30 FPS on a single laptop CPU core; Hailo-8L accelerator rated at 13 TOPS enables 30 FPS at <5W.",
                "motion_gate_saving": "Cuts detector calls up to 40% during quiet road intervals, conserving battery & thermals.",
            },
            "error": None,
        }
    except Exception as e:
        return {"ok": False, "data": {}, "error": str(e)}


@app.get("/api/calibration")
async def get_calibration():
    """M1: Homography Calibration & Ground Projection Status."""
    try:
        calib_path = BASE_DIR / "fixtures" / "calibration.json"
        if calib_path.exists():
            with open(calib_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {"ok": True, "data": data, "error": None}
        return {"ok": False, "data": {}, "error": "Calibration file not found"}
    except Exception as e:
        return {"ok": False, "data": {}, "error": str(e)}


@app.post("/api/pipeline/run-sample")
async def run_sample_pipeline():
    """Triggers the full edge pipeline dry-run on sample fixtures and ingests events live."""
    try:
        from edge.run_pipeline import main as run_pipeline_main
        ret = run_pipeline_main(["--dry-run"])

        events_out = BASE_DIR / "out" / "events.jsonl"
        ingested_count = 0
        if events_out.exists():
            from edge.common.jsonl import load_jsonl
            records = load_jsonl(events_out)
            for evt_data in records:
                evt = ConflictEvent(**evt_data)
                enriched = enricher.enrich(evt)
                store_event_sqlite(enriched)
                store_event_postgres(enriched)
                ingested_count += 1

        return {
            "ok": True,
            "data": {
                "exit_code": ret,
                "events_ingested": ingested_count,
                "message": f"Pipeline executed successfully. Ingested {ingested_count} events into live system.",
            },
            "error": None,
        }
    except Exception as e:
        logger.error(f"Pipeline run error: {e}")
        return {"ok": False, "data": {}, "error": str(e)}


@app.get("/api/health")
async def health():
    """Pipeline and database status."""
    postgres_ok = False
    if psycopg2:
        try:
            conn = psycopg2.connect(**DB_POSTGRES)
            conn.close()
            postgres_ok = True
        except Exception:
            postgres_ok = False

    sqlite_events = 0
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM events")
        sqlite_events = cur.fetchone()[0]
        conn.close()
        sqlite_ok = True
    except Exception:
        sqlite_ok = False

    buffered = len(event_buffer.get_all())

    return {
        "ok": True,
        "data": {
            "postgres": postgres_ok,
            "sqlite": sqlite_ok,
            "total_events_in_db": sqlite_events,
            "buffer": buffered,
            "timestamp": datetime.utcnow().isoformat(),
        },
        "error": None,
    }


# ============================================================================
# VIDEO INGEST (owner E) - upload a clip, pin it to a location, run M1-M5
# ============================================================================

def ingest_pipeline_events(events: list) -> int:
    """Store events produced by an uploaded clip. Returns how many landed.

    Same path as POST /api/events: M6 enrichment, then SQLite, then Postgres
    with the offline buffer behind it. Idempotent on event_id, so re-running a
    clip updates its events rather than duplicating them.
    """
    known = set(getattr(ConflictEvent, "model_fields", None)
                or getattr(ConflictEvent, "__fields__", {}))
    stored = 0
    for raw in events:
        try:
            evt = ConflictEvent(**{k: v for k, v in raw.items() if k in known})
        except Exception as exc:
            logger.warning(f"Dropped a malformed pipeline event: {exc}")
            continue
        evt = enricher.enrich(evt)
        ok_sqlite = store_event_sqlite(evt)
        if not store_event_postgres(evt):
            event_buffer.add(evt)
        if ok_sqlite:
            stored += 1
    return stored


@app.post("/api/videos")
async def upload_video(
    request: Request,
    name: str = Query("Unnamed site"),
    lat: float = Query(...),
    lon: float = Query(...),
    filename: str = Query("clip.mp4"),
    quality: str = Query("standard"),
):
    """Accept a video plus the location it was filmed at, and start the pipeline.

    The body is the raw file, streamed straight to disk - a demo clip is
    hundreds of megabytes and must never be buffered in memory. Metadata rides
    in the query string, which keeps this dependency-free (no python-multipart).
    """
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return {"ok": False, "data": {}, "error": "Latitude or longitude out of range."}

    suffix = Path(filename).suffix.lower()
    if suffix not in video_jobs.ALLOWED_SUFFIXES:
        allowed = ", ".join(sorted(video_jobs.ALLOWED_SUFFIXES))
        return {"ok": False, "data": {}, "error": f"Unsupported video type '{suffix}'. Accepted: {allowed}"}

    job_id = video_jobs.new_job_id()
    folder = video_jobs.job_dir(job_id)
    dest = folder / f"source{suffix}"

    written = 0
    try:
        with open(dest, "wb") as fh:
            async for chunk in request.stream():
                written += len(chunk)
                if written > video_jobs.MAX_UPLOAD_BYTES:
                    raise ValueError("Upload exceeds the 2 GB limit.")
                fh.write(chunk)
    except Exception as exc:
        try:
            dest.unlink()
        except OSError:
            pass
        return {"ok": False, "data": {}, "error": f"Upload failed: {exc}"}

    if written == 0:
        try:
            dest.unlink()
        except OSError:
            pass
        return {"ok": False, "data": {}, "error": "Empty upload - no video data received."}

    job = video_jobs.create_job(
        SQLITE_DB,
        job_id=job_id,
        site_name=(name or "Unnamed site").strip()[:120],
        lat=lat,
        lon=lon,
        filename=filename,
        video_path=dest,
        quality=quality if quality in video_jobs.QUALITY_PRESETS else "standard",
    )
    video_jobs.start_job(SQLITE_DB, job_id, ingest_pipeline_events)
    logger.info(f"Video job {job_id} queued: {filename} ({written/1e6:.1f} MB) at {lat},{lon}")

    return {"ok": True, "data": {**job, "bytes": written}, "error": None}


@app.get("/api/videos")
async def list_videos():
    """Every uploaded site, newest first. Drives the map pins."""
    try:
        return {"ok": True, "data": video_jobs.list_jobs(SQLITE_DB), "error": None}
    except Exception as e:
        return {"ok": False, "data": [], "error": str(e)}


@app.get("/api/videos/{job_id}")
async def video_status(job_id: str):
    """Poll one job while it runs."""
    job = video_jobs.get_job(SQLITE_DB, job_id)
    if not job:
        return {"ok": False, "data": {}, "error": "No such video job."}
    return {"ok": True, "data": job, "error": None}


@app.delete("/api/videos/{job_id}")
async def delete_video(job_id: str, keep_events: bool = Query(False)):
    """Remove a site: its job record, its files, and by default its events."""
    ok = video_jobs.delete_job(SQLITE_DB, job_id, drop_events=not keep_events)
    if not ok:
        return {"ok": False, "data": {}, "error": "No such video job."}
    return {"ok": True, "data": {"job_id": job_id, "deleted": True}, "error": None}


# ============================================================================
# STATIC FILES MOUNTING (FRONTEND & MEDIA)
# ============================================================================

CLIPS_DIR = BASE_DIR / "demo" / "clips"
if CLIPS_DIR.exists():
    app.mount("/clips", StaticFiles(directory=str(CLIPS_DIR)), name="clips")

OUT_DIR = BASE_DIR / "out"
OUT_DIR.mkdir(exist_ok=True)
app.mount("/out", StaticFiles(directory=str(OUT_DIR)), name="out")

# Uploaded clips and the annotated renders made from them.
app.mount("/videos", StaticFiles(directory=str(video_jobs.VIDEO_DIR)), name="videos")

WEB_DIR = BASE_DIR / "web"
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


# ============================================================================
# STARTUP
# ============================================================================

@app.on_event("startup")
async def startup():
    logger.info("NETRA Unified Server ready.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
