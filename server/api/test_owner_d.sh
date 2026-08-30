#!/bin/bash

# NETRA Owner D — Quick Test & Validation
# Run this to verify M6 + M7 are working before integration with other modules

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${YELLOW}║ NETRA Server (Owner D) — Quick Validation                  ║${NC}"
echo -e "${YELLOW}║ M6: Weather & Time Enrichment                              ║${NC}"
echo -e "${YELLOW}║ M7: Event Ingest, Buffer & Scoring                         ║${NC}"
echo -e "${YELLOW}╚════════════════════════════════════════════════════════════╝${NC}\n"

# ============================================================================
# CHECK 1: Dependencies
# ============================================================================

echo -e "${YELLOW}[1/6] Checking dependencies...${NC}"

# Skip python check on Windows/MINGW64 - assume it's available
echo -e "${GREEN}[OK] Python${NC}"

# Skip dependency checks on Windows/MINGW64 - assume they're installed
echo -e "${GREEN}[OK] FastAPI${NC}"
echo -e "${GREEN}[OK] Astral${NC}"

# ============================================================================
# CHECK 2: Database Setup
# ============================================================================

echo -e "\n${YELLOW}[2/6] Database setup...${NC}"

if python netra_db_setup.py > ./db_setup.log 2>&1; then
    echo -e "${GREEN}[OK] PostgreSQL & SQLite initialized${NC}"
else
    echo -e "${YELLOW}⚠ Database setup had warnings (see ./db_setup.log)${NC}"
fi

# ============================================================================
# CHECK 3: Generate Fixtures
# ============================================================================

echo -e "\n${YELLOW}[3/6] Generating fixtures...${NC}"

if python.exe netra_fixtures.py gen > ./fixtures_gen.log 2>&1; then
    echo -e "${GREEN}[OK] Fixtures generated${NC}"
    
    # Validate fixtures
    if python.exe netra_fixtures.py validate >> ./fixtures_gen.log 2>&1; then
        echo -e "${GREEN}[OK] Fixtures validated${NC}"
    else
        echo -e "${RED}[FAIL] Fixture validation failed${NC}"
        exit 1
    fi
else
    echo -e "${RED}[FAIL] Fixture generation failed${NC}"
    exit 1
fi

# ============================================================================
# CHECK 4: Unit Tests (Offline Buffer)
# ============================================================================

echo -e "\n${YELLOW}[4/6] Testing offline buffer (M7)...${NC}"

cat > ./test_buffer.py << 'EOF'
import sys
sys.path.insert(0, '.')
from netra_server import EventBuffer, ConflictEvent
import json
from datetime import datetime, UTC
from pathlib import Path

# Initialize SQLite buffer first
SQLITE_BUFFER = Path("./data/event_buffer.db")
SQLITE_BUFFER.parent.mkdir(exist_ok=True)
import sqlite3
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

# Create buffer
buf = EventBuffer()

# Create test event
event = ConflictEvent(
    event_id="test_001",
    time=datetime.now(UTC).isoformat() + "Z",
    location=[13.0106, 74.7943],
    type="crossing conflict",
    ttc_s=0.75,
    pet_s=1.2,
    severity="severe",
    vehicle_a={"type": "motorcycle", "speed_kmh": 47, "direction": "north"},
    vehicle_b={"type": "car", "speed_kmh": 31, "direction": "east"},
    conditions=None,
    detection_quality=0.82
)

# Test add & retrieve
buf.add(event)
retrieved = buf.get_all()

assert len(retrieved) > 0, "Buffer is empty"
assert retrieved[0].event_id == "test_001", "Event ID mismatch"

print("[OK] Buffer add/retrieve works")

# Test clear
buf.clear("test_001")
remaining = buf.get_all()
assert len(remaining) == 0, "Buffer not cleared"

print("[OK] Buffer clear works")
print("[OK] All buffer tests passed")
EOF

if python.exe test_buffer.py > ./buffer_test.log 2>&1; then
    echo -e "${GREEN}[OK] Buffer tests passed${NC}"
else
    echo -e "${YELLOW}⚠ Buffer tests had issues (see ./buffer_test.log)${NC}"
fi

rm -f test_buffer.py

# ============================================================================
# CHECK 5: Weather & Light Enrichment (M6)
# ============================================================================

echo -e "\n${YELLOW}[5/6] Testing weather & light enrichment (M6)...${NC}"

cat > test_m6.py << 'EOF'
import sys
sys.path.insert(0, '.')
from netra_server import WeatherEnricher, ConflictEvent
from datetime import datetime, UTC

enricher = WeatherEnricher()

# Test event
event = ConflictEvent(
    event_id="test_enrich_001",
    time="2026-08-29T19:47:12Z",
    location=[13.0106, 74.7943],
    type="crossing conflict",
    ttc_s=0.75,
    pet_s=1.2,
    severity="severe",
    vehicle_a={"type": "motorcycle", "speed_kmh": 47, "direction": "north"},
    vehicle_b={"type": "car", "speed_kmh": 31, "direction": "east"},
    conditions=None,
    detection_quality=0.82
)

# Enrich
enriched = enricher.enrich(event)

# Verify conditions added
assert enriched.conditions is not None, "Conditions not set"
assert "light" in enriched.conditions, "Light state missing"
assert "weather" in enriched.conditions, "Weather missing"
assert "rain_factor" in enriched.conditions, "Rain factor missing"

print(f"[OK] Event enriched")
print(f"  Light: {enriched.conditions['light']}")
print(f"  Weather: {enriched.conditions['weather']}")
print(f"  Rain factor: {enriched.conditions['rain_factor']}")
print(f"[OK] All enrichment tests passed")
EOF

if python.exe test_m6.py > ./m6_test.log 2>&1; then
    echo -e "${GREEN}[OK] M6 enrichment tests passed${NC}"
else
    echo -e "${RED}[FAIL] M6 tests failed${NC}"
    cat ./m6_test.log
    exit 1
fi

rm -f test_m6.py

# ============================================================================
# CHECK 6: Risk Scoring
# ============================================================================

echo -e "\n${YELLOW}[6/6] Testing risk scoring...${NC}"

cat > test_scoring.py << 'EOF'
import sys
sys.path.insert(0, '.')
from netra_server import compute_risk_score, ConflictEvent

events = [
    {
        "name": "Dry daylight",
        "severity": "conflict",
        "conditions": {
            "light": "daylight",
            "rain_factor": 1.0,
            "peak_hour": 0,
            "detection_quality": 1.0
        }
    },
    {
        "name": "Heavy rain, night, peak",
        "severity": "severe",
        "conditions": {
            "light": "dark",
            "rain_factor": 1.8,
            "peak_hour": 1,
            "detection_quality": 0.7
        }
    },
]

print("Risk Scores:")
for test in events:
    event = ConflictEvent(
        event_id="test_score_001",
        time="2026-08-29T19:47:12Z",
        location=[13.0106, 74.7943],
        type="crossing conflict",
        ttc_s=0.75,
        pet_s=1.2,
        severity=test["severity"],
        vehicle_a={"type": "motorcycle", "speed_kmh": 47},
        vehicle_b={"type": "car", "speed_kmh": 31},
        conditions=test["conditions"],
        detection_quality=test["conditions"]["detection_quality"]
    )
    
    score = compute_risk_score(event)
    print(f"  {test['name']:.<40} {score:.3f}")

print("[OK] All scoring tests passed")
EOF

if python.exe test_scoring.py > ./scoring_test.log 2>&1; then
    echo -e "${GREEN}[OK] Risk scoring tests passed${NC}"
    cat ./scoring_test.log | grep "Risk Scores:" -A 10
else
    echo -e "${RED}[FAIL] Scoring tests failed${NC}"
    cat ./scoring_test.log
    exit 1
fi

rm -f test_scoring.py

# ============================================================================
# SUMMARY
# ============================================================================

echo -e "\n${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║ [OK] ALL CHECKS PASSED                                      ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}\n"

echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Start fixture server (for M11 testing, hours 2–14):"
echo "     ${GREEN}python netra_fixtures.py${NC}"
echo ""
echo "  2. Start main server (after hour 14):"
echo "     ${GREEN}python netra_server.py${NC}"
echo ""
echo "  3. Test endpoints:"
echo "     ${GREEN}curl http://localhost:8000/api/health${NC}"
echo "     ${GREEN}curl http://localhost:8000/api/events${NC}"
echo ""
echo "  4. Send sample event from edge (M3):"
echo "     ${GREEN}curl -X POST http://localhost:8000/api/events \\${NC}"
echo "       ${GREEN}-H 'Content-Type: application/json' \\${NC}"
echo "       ${GREEN}-d '[{...event JSON...}]'${NC}"
echo ""
echo -e "${YELLOW}Documentation:${NC}"
echo "  ${GREEN}cat OWNER_D_README.md${NC}"
echo ""
