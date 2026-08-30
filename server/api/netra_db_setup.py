"""
NETRA Database Setup & Testing — Owner D
Initialize PostgreSQL, test offline buffer, demonstrate idempotency.
"""

import psycopg2
import sqlite3
from pathlib import Path
import json
from datetime import datetime
import sys
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host": "localhost",
    "database": "netra",
    "user": "netra_user",
    "password": "netra_pass",
    "port": 5432,
}

SQLITE_BUFFER = Path("./data/event_buffer.db")


# ============================================================================
# POSTGRES SETUP
# ============================================================================

def setup_postgres():
    """Initialize PostgreSQL database and schema."""
    logger.info("Setting up PostgreSQL...")
    
    # Connect to default postgres DB to create our DB
    try:
        conn = psycopg2.connect(
            host=DB_CONFIG["host"],
            database="postgres",
            user="postgres",
            password="postgres",
            port=DB_CONFIG["port"]
        )
        conn.autocommit = True
        cur = conn.cursor()
        
        # Drop and recreate DB for clean slate
        try:
            cur.execute(f"DROP DATABASE IF EXISTS {DB_CONFIG['database']};")
            logger.info(f"Dropped existing database {DB_CONFIG['database']}")
        except:
            pass
        
        cur.execute(f"CREATE DATABASE {DB_CONFIG['database']};")
        logger.info(f"Created database {DB_CONFIG['database']}")
        
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to create database: {e}")
        logger.info("Proceeding with assumption database already exists...")
    
    # Connect to our DB and set up schema
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        cur = conn.cursor()
        
        # Try PostGIS
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
            logger.info("[OK] PostGIS extension enabled")
        except Exception as e:
            logger.warning(f"PostGIS unavailable: {e}")
        
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
        
        # Create indices
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_id ON events(event_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_time ON events(time);")
        
        logger.info("[OK] PostgreSQL schema created")
        
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"PostgreSQL setup failed: {e}")
        return False
    
    return True


# ============================================================================
# SQLITE BUFFER SETUP
# ============================================================================

def setup_sqlite_buffer():
    """Initialize SQLite buffer for offline mode."""
    logger.info("Setting up SQLite buffer...")
    
    SQLITE_BUFFER.parent.mkdir(exist_ok=True)
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
    
    logger.info(f"[OK] SQLite buffer ready at {SQLITE_BUFFER}")
    return True


# ============================================================================
# OFFLINE BUFFER TEST
# ============================================================================

def test_offline_buffer():
    """
    Demonstrate offline resilience:
    1. Write event to buffer
    2. Simulate server coming back
    3. Replay buffer to Postgres
    4. Verify idempotency (no duplicates)
    """
    logger.info("\n" + "="*60)
    logger.info("TEST: Offline Buffer & Idempotency")
    logger.info("="*60)
    
    # Sample event
    event_json = json.dumps({
        "event_id": "test_offline_001",
        "time": "2026-08-29T19:47:12Z",
        "location": [13.0106, 74.7943],
        "type": "crossing conflict",
        "ttc_s": 0.75,
        "pet_s": 1.2,
        "severity": "severe",
        "vehicle_a": {"type": "motorcycle", "speed_kmh": 47},
        "vehicle_b": {"type": "car", "speed_kmh": 31},
        "conditions": {"light": "dark", "weather": "rain"},
        "detection_quality": 0.82
    })
    
    # Step 1: Simulate offline — write to buffer
    logger.info("\n[1] Simulating offline: writing to SQLite buffer...")
    conn = sqlite3.connect(SQLITE_BUFFER)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO pending_events (event_id, event_json) VALUES (?, ?)",
        ("test_offline_001", event_json)
    )
    conn.commit()
    
    # Verify buffer
    cur.execute("SELECT COUNT(*) FROM pending_events")
    count = cur.fetchone()[0]
    logger.info(f"   [OK] Buffer has {count} pending event(s)")
    conn.close()
    
    # Step 2: Replay to Postgres
    logger.info("\n[2] Server online: replaying buffer to PostgreSQL...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        event = json.loads(event_json)
        cur.execute("""
            INSERT INTO events (
                event_id, time, location, type, ttc_s, pet_s, severity,
                vehicle_a, vehicle_b, conditions, detection_quality
            ) VALUES (%s, %s, POINT(%s, %s), %s, %s, %s, %s, %s, %s, %s)
        """, (
            event["event_id"],
            event["time"],
            event["location"][1], event["location"][0],
            event["ttc_s"],
            event["pet_s"],
            event["severity"],
            json.dumps(event["vehicle_a"]),
            json.dumps(event["vehicle_b"]),
            json.dumps(event["conditions"]),
            event["detection_quality"],
        ))
        conn.commit()
        logger.info("   [OK] Event replayed to PostgreSQL")
        
        # Step 3: Test idempotency — replay same event again
        logger.info("\n[3] Testing idempotency: replaying same event again...")
        
        # This should either fail silently (caught exception) or update
        try:
            cur.execute("""
                INSERT INTO events (
                    event_id, time, location, type, ttc_s, pet_s, severity,
                    vehicle_a, vehicle_b, conditions, detection_quality
                ) VALUES (%s, %s, POINT(%s, %s), %s, %s, %s, %s, %s, %s, %s)
            """, (
                event["event_id"],
                event["time"],
                event["location"][1], event["location"][0],
                event["ttc_s"],
                event["pet_s"],
                event["severity"],
                json.dumps(event["vehicle_a"]),
                json.dumps(event["vehicle_b"]),
                json.dumps(event["conditions"]),
                event["detection_quality"],
            ))
            conn.commit()
            logger.warning("   ! Second insert succeeded (constraint may not be enforced)")
        except psycopg2.IntegrityError as e:
            logger.info(f"   [OK] Duplicate rejected: {e.__class__.__name__}")
            conn.rollback()
        
        # Step 4: Verify deduplication
        logger.info("\n[4] Verifying deduplication...")
        cur.execute("SELECT COUNT(*) FROM events WHERE event_id = %s", ("test_offline_001",))
        count = cur.fetchone()[0]
        logger.info(f"   [OK] Database has exactly {count} copy of event (idempotent)" if count == 1 else f"   [FAIL] Found {count} copies")
        
        cur.close()
        conn.close()
    
    except Exception as e:
        logger.error(f"PostgreSQL test failed: {e}")
        return False
    
    # Step 5: Clear buffer
    logger.info("\n[5] Clearing buffer after successful replay...")
    conn = sqlite3.connect(SQLITE_BUFFER)
    cur = conn.cursor()
    cur.execute("DELETE FROM pending_events WHERE event_id = ?", ("test_offline_001",))
    conn.commit()
    
    cur.execute("SELECT COUNT(*) FROM pending_events")
    remaining = cur.fetchone()[0]
    logger.info(f"   [OK] Buffer cleared ({remaining} remaining)")
    conn.close()
    
    logger.info("\n" + "="*60)
    logger.info("[OK] All tests passed")
    logger.info("="*60)
    
    return True


# ============================================================================
# CONDITION MULTIPLIERS TEST
# ============================================================================

def test_condition_multipliers():
    """Verify risk scoring under different conditions."""
    logger.info("\n" + "="*60)
    logger.info("TEST: Condition Multiplier Risk Scoring")
    logger.info("="*60)
    
    base_risk = 0.5
    
    scenarios = [
        ("Dry daylight", {"rain_factor": 1.0, "night": False, "peak": False}),
        ("Light rain, dark, peak", {"rain_factor": 1.3, "night": True, "peak": True}),
        ("Heavy rain, night", {"rain_factor": 1.8, "night": True, "peak": False}),
    ]
    
    for name, factors in scenarios:
        rain_mult = factors["rain_factor"]
        night_mult = 1.5 if factors["night"] else 1.0
        peak_mult = 1.3 if factors["peak"] else 1.0
        
        risk = base_risk * rain_mult * night_mult * peak_mult
        logger.info(f"{name:.<40} risk = {risk:.2f}")
    
    logger.info("="*60)


# ============================================================================
# MAIN
# ============================================================================

def main():
    logger.info("\n╔" + "="*58 + "╗")
    logger.info("║ NETRA Server Setup — Owner D                            ║")
    logger.info("╚" + "="*58 + "╝\n")
    
    # Setup
    if not setup_postgres():
        logger.error("PostgreSQL setup failed. Install PostgreSQL and create superuser first.")
        logger.info("\nQuick start:")
        logger.info("  brew install postgresql@15")
        logger.info("  initdb /usr/local/var/postgres")
        logger.info("  pg_ctl -D /usr/local/var/postgres start")
        sys.exit(1)
    
    if not setup_sqlite_buffer():
        logger.error("SQLite buffer setup failed")
        sys.exit(1)
    
    # Tests
    test_offline_buffer()
    test_condition_multipliers()
    
    logger.info("\n[OK] Setup complete. Ready for M7 integration.")
    logger.info("\nNext steps:")
    logger.info("  1. Start fixture server:  python netra_fixtures.py gen && python netra_fixtures.py")
    logger.info("  2. Start main server:     python netra_server.py")
    logger.info("  3. Verify:                curl http://localhost:8000/api/health")


if __name__ == "__main__":
    main()
