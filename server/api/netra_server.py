"""
NETRA Server — Owner D
M6: Weather & Time Enrichment
M7: Event Ingest, Buffer, Scoring
"""

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import sqlite3
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, List, Dict, Any
import astral
import astral.sun
import logging
from pydantic import BaseModel
import threading
import time
from pathlib import Path

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

SQLITE_BUFFER = Path("./data/event_buffer.db")
SQLITE_BUFFER.parent.mkdir(exist_ok=True)

# [PATCH E-10] Keyed by condition only. It was keyed "{hour:02d}_{condition}"
# but only "00_*" entries existed, so every lookup for any hour but midnight
# missed and fell back to dry/clear. Every event came back clear regardless of
# the intended weather, which silently disabled the rain condition filter —
# PRD success criterion S5 ("the same junction shows different risk under
# different condition filters").
WEATHER_LOOKUP = {
    "dry": {"rain_factor": 1.0, "surface": "dry", "weather": "clear"},
    "light_rain": {"rain_factor": 1.3, "surface": "wet", "weather": "light rain"},
    "heavy_rain": {"rain_factor": 1.8, "surface": "wet", "weather": "heavy rain"},
    "fog": {"rain_factor": 1.5, "surface": "dry", "weather": "fog"},
}

# [PATCH E-10] Cached per-hour observations for the demo clip's date, which is
# what the PRD asks for (M6: "Weather from a cached lookup table committed to
# the repo. A live API is a bonus path, never the demo path.").
#
# Replace these with real observations for the actual clip once PRD Q1 is
# settled. Keys are "YYYY-MM-DD HH"; anything not listed falls through to the
# diurnal default below.
WEATHER_CACHE = {
    # Evening rain on the demo date, clearing after midnight.
    "2026-08-28 17": "light_rain",
    "2026-08-28 18": "light_rain",
    "2026-08-28 19": "heavy_rain",
    "2026-08-28 20": "light_rain",
    "2026-08-28 21": "light_rain",
    "2026-08-28 22": "dry",
}

LOCATION_BASE_RISK = {
    # junction_id -> base_risk_score
    "default": 0.5,
}

# [PATCH E-3] Clustering resolution for /api/segments.
# 2 decimals ~ 1.1 km (one cluster per town), 4 ~ 11 m (per approach).
SEGMENT_GRID_DECIMALS = 4
# Widened from 24 hours: a demo clip is a single evening, so a 24-hour
# window silently returns nothing the next morning.
SEGMENT_WINDOW = "30 days"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI()

# [PATCH E-1] CORS. The M11 dashboard is served from a different origin
# (file:// or another localhost port), so without this every fetch from the
# browser is blocked before it reaches a handler. Demo-wide open policy is
# fine here: the service is local, read-mostly and unauthenticated by design
# (PRD Section 3: no auth in scope). Narrow this if it is ever exposed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# PYDANTIC MODELS (from contracts)
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
# DATABASE SETUP
# ============================================================================

def init_postgres():
    """Initialize PostgreSQL with PostGIS (or fall back to plain Postgres)."""
    try:
        conn = psycopg2.connect(**DB_POSTGRES)
        cur = conn.cursor()
        
        # Try to enable PostGIS
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
            conn.commit()
            logger.info("PostGIS enabled")
        except Exception as e:
            # [PATCH E-7] Without this rollback the failed CREATE EXTENSION
            # leaves the transaction aborted, so the CREATE TABLE below is
            # skipped with "current transaction is aborted" and the events
            # table is never created — the server then looks healthy while
            # storing nothing. PostGIS is genuinely optional here: the demo
            # does not need spatial indexing at this data volume (PRD M7).
            conn.rollback()
            logger.warning(f"PostGIS unavailable, using plain Postgres ({e})")
        
        # Create events table
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
                ingested_at TIMESTAMP DEFAULT NOW()
            );
        """)
        
        # Create index on event_id for idempotency
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_id ON events(event_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_time ON events(time);")
        
        conn.commit()
        conn.close()
        logger.info("PostgreSQL initialized")
    except Exception as e:
        logger.error(f"PostgreSQL init failed: {e}. Using SQLite fallback only.")


def init_sqlite_buffer():
    """Initialize SQLite buffer for offline resilience."""
    conn = sqlite3.connect(SQLITE_BUFFER)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_events (
            event_id TEXT PRIMARY KEY,
            event_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
    logger.info(f"SQLite buffer initialized at {SQLITE_BUFFER}")


# ============================================================================
# HELPERS
# ============================================================================

def parse_point(value):
    """
    [PATCH E-9] psycopg2 returns a native Postgres POINT as the string
    "(lon,lat)", not a tuple. The original code did row[2][1], row[2][0],
    which indexed two characters of that string, so every event came back
    with a location like ['7', '('] and nothing could be placed on the map.
    Returns [lat, lon] to match the ConflictEvent contract (Section 5.3).
    """
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return [float(value[1]), float(value[0])]  # stored as (lon, lat)
    if isinstance(value, str):
        try:
            lon, lat = value.strip("()").split(",")
            return [float(lat), float(lon)]
        except (ValueError, AttributeError):
            return None
    return None


def as_dict(value):
    """
    [PATCH E-8] psycopg2 already decodes JSONB columns into Python dicts, so
    calling json.loads() on them raised "the JSON object must be str, bytes
    or bytearray, not dict" and broke GET /api/events and the narrative
    endpoint. Accept either form so the code works whichever adapter is in
    use (and with the SQLite buffer, where values really are strings).
    """
    if value is None:
        return {}
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value


# ============================================================================
# M7: EVENT BUFFERING & INGEST
# ============================================================================

class EventBuffer:
    """Buffer events locally when Postgres is unavailable."""
    
    def __init__(self, db_path=SQLITE_BUFFER):
        self.db_path = db_path
    
    def add(self, event: ConflictEvent):
        """Buffer an event to SQLite."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO pending_events (event_id, event_json) VALUES (?, ?)",
            (event.event_id, event.json())
        )
        conn.commit()
        conn.close()
    
    def get_all(self) -> List[ConflictEvent]:
        """Retrieve all buffered events."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT event_json FROM pending_events ORDER BY created_at ASC")
        rows = cur.fetchall()
        conn.close()
        return [ConflictEvent(**json.loads(row[0])) for row in rows]
    
    def clear(self, event_id: str):
        """Remove an event after successful replay."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM pending_events WHERE event_id = ?", (event_id,))
        conn.commit()
        conn.close()


event_buffer = EventBuffer()


def store_event_postgres(event: ConflictEvent) -> bool:
    """Store event in PostgreSQL. Return True on success."""
    try:
        conn = psycopg2.connect(**DB_POSTGRES)
        cur = conn.cursor()
        
        # Idempotent insert: check if already exists
        cur.execute("SELECT 1 FROM events WHERE event_id = %s", (event.event_id,))
        if cur.fetchone():
            logger.info(f"Event {event.event_id} already exists; skipping duplicate")
            conn.close()
            return True  # Not an error; idempotent
        
        # Parse timestamp
        event_time = datetime.fromisoformat(event.time.replace('Z', '+00:00'))
        
        cur.execute("""
            INSERT INTO events (
                event_id, time, location, type, ttc_s, pet_s, severity,
                vehicle_a, vehicle_b, conditions, detection_quality
            ) VALUES (%s, %s, POINT(%s, %s), %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            event.event_id,
            event_time,
            event.location[1], event.location[0],  # (lon, lat) for PostGIS
            event.type,
            event.ttc_s,
            event.pet_s,
            event.severity,
            json.dumps(event.vehicle_a),
            json.dumps(event.vehicle_b),
            json.dumps(event.conditions),
            event.detection_quality,
        ))
        conn.commit()
        conn.close()
        logger.info(f"Event {event.event_id} stored in PostgreSQL")
        return True
    except Exception as e:
        logger.error(f"PostgreSQL store failed: {e}")
        return False


def replay_buffer():
    """Background thread: replay buffered events when Postgres comes back online."""
    while True:
        time.sleep(30)  # Check every 30 seconds
        buffered = event_buffer.get_all()
        if not buffered:
            continue
        
        logger.info(f"Attempting to replay {len(buffered)} buffered events...")
        for event in buffered:
            if store_event_postgres(event):
                event_buffer.clear(event.event_id)
        logger.info("Buffer replay cycle complete")


# Start replay thread
replay_thread = threading.Thread(target=replay_buffer, daemon=True)
replay_thread.start()


# ============================================================================
# M6: WEATHER & TIME ENRICHMENT
# ============================================================================

class WeatherEnricher:
    """Add weather, light, and time conditions to events."""
    
    def __init__(self, location_lat=13.0106, location_lon=74.7943):
        self.location = astral.LocationInfo(
            name="Demo",
            region="India",
            timezone="Asia/Kolkata",
            latitude=location_lat,
            longitude=location_lon
        )
    
    def get_light_state(self, dt: datetime) -> str:
        """Determine light state (daylight, dusk, dark) using astral."""
        # [PATCH E-6] astral returns timezone-aware datetimes. Event times
        # arrive as naive ISO strings, and comparing naive to aware raises
        # "can't compare offset-naive and offset-aware datetimes", which
        # aborted every ingest. Assume naive timestamps are local to the
        # observer, which is what the edge actually records.
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
    
    def get_weather(self, hour: int, location: List[float],
                    dt: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Look up weather from the cached table committed to this repo.

        [PATCH E-10] Prefers a real cached observation for the event's
        date and hour; otherwise falls back to a coarse diurnal guess.
        Never calls a live API — the demo must not depend on the network.
        """
        if dt is not None:
            cached = WEATHER_CACHE.get(f"{dt.strftime('%Y-%m-%d')} {hour:02d}")
            if cached:
                return WEATHER_LOOKUP[cached]

        # Fallback when the hour is not in the cache. Monsoon evenings in
        # coastal Karnataka are wet more often than not, but this is a guess
        # and is flagged as such rather than presented as an observation.
        condition = "light_rain" if (hour >= 19 or hour < 6) else "dry"
        out = dict(WEATHER_LOOKUP[condition])
        out["estimated"] = True
        return out
    
    def enrich(self, event: ConflictEvent) -> ConflictEvent:
        """Add conditions to event."""
        dt = datetime.fromisoformat(event.time.replace('Z', '+00:00'))
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
            # True when no cached observation existed for this hour, so the
            # dashboard can distinguish measured conditions from guessed ones.
            "weather_estimated": weather_data.get("estimated", False),
            "detection_quality": event.detection_quality,
        }
        
        event.conditions = conditions
        return event


enricher = WeatherEnricher()


# ============================================================================
# RISK SCORING
# ============================================================================

def compute_risk_score(event: ConflictEvent) -> float:
    """
    Compute risk = base_risk(location) × rain_factor × night_factor × peak_factor
    """
    base_risk = LOCATION_BASE_RISK.get("default", 0.5)
    
    # Severity multiplier
    severity_mult = 2.0 if event.severity == "severe" else 1.0
    
    # Condition multipliers
    conditions = event.conditions or {}
    rain_factor = conditions.get("rain_factor", 1.0)
    night_factor = 1.5 if conditions.get("light") == "dark" else 1.0
    peak_factor = 1.3 if conditions.get("peak_hour") else 1.0
    
    # Normalise by detection quality (lower quality → lower risk confidence)
    quality_norm = conditions.get("detection_quality", 0.8)
    
    risk = base_risk * severity_mult * rain_factor * night_factor * peak_factor * quality_norm
    
    return min(risk, 1.0)  # Clamp to [0, 1]


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.post("/api/events")
async def ingest_events(events: List[ConflictEvent]):
    """Ingest one or more ConflictEvents."""
    results = []
    
    for event in events:
        # Enrich with weather & time
        event = enricher.enrich(event)
        
        # Try Postgres; buffer if it fails
        success = store_event_postgres(event)
        if not success:
            event_buffer.add(event)
            logger.warning(f"Event {event.event_id} buffered due to Postgres failure")
        
        results.append({"event_id": event.event_id, "ok": success})
    
    return {"ok": True, "data": results, "error": None}


@app.get("/api/events")
async def list_events(
    from_time: Optional[str] = Query(None),
    to_time: Optional[str] = Query(None),
    light: Optional[str] = Query(None),
    weather: Optional[str] = Query(None),
):
    """Filtered event list with condition filters."""
    try:
        conn = psycopg2.connect(**DB_POSTGRES)
        cur = conn.cursor()
        
        query = "SELECT * FROM events WHERE 1=1"
        params = []
        
        if from_time:
            query += " AND time >= %s"
            params.append(from_time)
        if to_time:
            query += " AND time <= %s"
            params.append(to_time)
        
        cur.execute(query + " ORDER BY time DESC", params)
        rows = cur.fetchall()
        
        # Reconstruct as dicts
        results = []
        for row in rows:
            conditions = as_dict(row[9])
            
            # Apply condition filters
            if light and conditions.get("light") != light:
                continue
            if weather and conditions.get("weather") != weather:
                continue
            
            results.append({
                "event_id": row[0],
                "time": row[1].isoformat() if row[1] else None,
                "location": parse_point(row[2]),  # [lat, lon]
                "type": row[3],
                "ttc_s": row[4],
                "pet_s": row[5],
                "severity": row[6],
                "vehicle_a": as_dict(row[7]),
                "vehicle_b": as_dict(row[8]),
                "conditions": conditions,
                "detection_quality": row[10],
            })
        
        conn.close()
        return {"ok": True, "data": results, "error": None}
    
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return {"ok": False, "data": [], "error": str(e)}


@app.get("/api/events/{event_id}/narrative")
async def get_narrative(event_id: str):
    """
    Plain-English incident write-up.
    In production, this would call M8 (narration engine).
    For now, return a template.
    """
    try:
        conn = psycopg2.connect(**DB_POSTGRES)
        cur = conn.cursor()
        cur.execute("SELECT * FROM events WHERE event_id = %s", (event_id,))
        row = cur.fetchone()
        conn.close()
        
        if not row:
            raise HTTPException(status_code=404, detail="Event not found")
        
        # Simple template (M8 would generate this)
        conditions = as_dict(row[9])
        
        # [PATCH E-4] vehicle_a is row[7] and vehicle_b is row[8]; these were
        # swapped, so every narrative named the wrong vehicle first.
        # [PATCH E-5] ttc_s is nullable, and "{None:.1f}" raises TypeError.
        vehicle_a = as_dict(row[7])
        vehicle_b = as_dict(row[8])
        ttc = f"{row[4]:.1f}s" if row[4] is not None else "an unrecorded interval"

        narrative = (
            f"At {row[1].strftime('%I:%M %p')} on {row[1].strftime('%A')}, "
            f"({'after dark' if conditions.get('light') == 'dark' else 'in daylight'}) "
            f"and in {conditions.get('weather', 'unknown conditions')}, "
            f"a {vehicle_a.get('type', 'vehicle')} travelling at {vehicle_a.get('speed_kmh', '?')} km/h "
            f"approached as a {vehicle_b.get('type', 'vehicle')} entered with TTC {ttc}."
        )
        
        return {"ok": True, "data": {"narrative": narrative}, "error": None}
    
    except Exception as e:
        logger.error(f"Narrative fetch failed: {e}")
        return {"ok": False, "data": {}, "error": str(e)}


@app.get("/api/segments")
async def get_segments():
    """Scored road segments for the map."""
    try:
        conn = psycopg2.connect(**DB_POSTGRES)
        cur = conn.cursor()
        
        # [PATCH E-2] `location` is a native Postgres POINT, not a PostGIS
        # geometry, so ST_Y()/ST_X() raised "function st_y(point) does not
        # exist" and this endpoint returned ok:false on every request.
        # Native POINT is subscripted: location[0] = x (lon), [1] = y (lat).
        #
        # [PATCH E-3] The grid was ROUND(...,2) = 0.01 deg ~ 1.1 km, which
        # collapses a whole junction into one segment. SEGMENT_GRID_DECIMALS
        # = 4 is ~11 m and keeps the approaches distinct.
        cur.execute("""
            SELECT
                ROUND(location[1]::numeric, %s) as lat,
                ROUND(location[0]::numeric, %s) as lon,
                COUNT(*) as conflict_count,
                AVG(CASE WHEN severity='severe' THEN 2.0 ELSE 1.0 END) as risk_score,
                json_agg(conditions) as conditions_list
            FROM events
            WHERE time > NOW() - INTERVAL %s
            GROUP BY lat, lon
            ORDER BY risk_score DESC
        """, (SEGMENT_GRID_DECIMALS, SEGMENT_GRID_DECIMALS, SEGMENT_WINDOW))
        
        rows = cur.fetchall()
        conn.close()
        
        segments = []
        for row in rows:
            segments.append({
                "segment_id": f"seg_{row[0]}_{row[1]}",
                "location": [row[0], row[1]],
                "risk_score": float(row[3] or 0.5),
                "conflict_count_24h": row[2],
                "conditions_applied": {"light": "mixed", "weather": "mixed"},
            })
        
        return {"ok": True, "data": segments, "error": None}
    
    except Exception as e:
        logger.error(f"Segments fetch failed: {e}")
        return {"ok": False, "data": [], "error": str(e)}


@app.get("/api/health")
async def health():
    """Pipeline status for the live panel."""
    try:
        conn = psycopg2.connect(**DB_POSTGRES)
        conn.close()
        postgres_ok = True
    except:
        postgres_ok = False
    
    buffered = len(event_buffer.get_all())
    
    return {
        "ok": True,
        "data": {
            "postgres": postgres_ok,
            "buffer": buffered,
            "timestamp": datetime.utcnow().isoformat(),
        },
        "error": None,
    }


# ============================================================================
# STARTUP
# ============================================================================

@app.on_event("startup")
async def startup():
    init_postgres()
    init_sqlite_buffer()
    logger.info("NETRA Server started")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
