# NETRA HTTP API Contract Specification

> **Frozen Interface Contract — PRD Section 5.5 Formalization**  
> **Status**: APPROVED & FROZEN  
> **Rule**: Every endpoint strictly uses the envelope `{"ok": true, "data": {...}, "error": null}`.

---

## 1. Global Response Envelope

All API endpoints MUST respond with this exact envelope structure:

### Success Response:
```json
{
  "ok": true,
  "data": {},
  "error": null
}
```

### Error Response (HTTP 4xx / 5xx):
```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "ERROR_CODE_STRING",
    "message": "Human readable error description."
  }
}
```

---

## 2. Endpoints Specification

### 2.1 Ingest Conflict Events
- **Method**: `POST`
- **Path**: `/api/events`
- **Owner**: Lane D (Server)
- **Request Body**: A single `ConflictEvent` object or an array `[ConflictEvent, ...]`:
  ```json
  {
    "event_id": "evt_00417",
    "time": "2026-08-28T19:47:12",
    "location": [13.0106, 74.7943],
    "type": "crossing conflict",
    "ttc_s": 0.8,
    "pet_s": 1.4,
    "severity": "severe",
    "vehicle_a": {
      "type": "motorcycle",
      "speed_kmh": 47.0,
      "direction": "normal"
    },
    "vehicle_b": {
      "type": "car",
      "speed_kmh": 31.0,
      "direction": "against flow"
    },
    "conditions": null,
    "detection_quality": 0.71
  }
  ```
- **Response (`201 Created` or `200 OK`)**:
  ```json
  {
    "ok": true,
    "data": {
      "ingested_count": 1,
      "event_ids": ["evt_00417"],
      "status": "stored"
    },
    "error": null
  }
  ```

---

### 2.2 Filtered Conflict Event List
- **Method**: `GET`
- **Path**: `/api/events`
- **Query Parameters**:
  - `from`: ISO 8601 string (e.g. `2026-08-28T00:00:00Z`)
  - `to`: ISO 8601 string
  - `light`: `daylight` | `dusk` | `dark`
  - `weather`: `dry` | `light rain` | `heavy rain` | `fog`
  - `severity`: `conflict` | `severe`
  - `limit`: integer (default `50`, max `500`)
  - `offset`: integer (default `0`)
- **Response (`200 OK`)**:
  ```json
  {
    "ok": true,
    "data": {
      "total_count": 8,
      "limit": 50,
      "offset": 0,
      "events": [
        {
          "event_id": "evt_00417",
          "time": "2026-08-28T19:47:12",
          "location": [13.0106, 74.7943],
          "type": "crossing conflict",
          "ttc_s": 0.8,
          "pet_s": 1.4,
          "severity": "severe",
          "vehicle_a": {
            "type": "motorcycle",
            "speed_kmh": 47.0,
            "direction": "normal"
          },
          "vehicle_b": {
            "type": "car",
            "speed_kmh": 31.0,
            "direction": "against flow"
          },
          "conditions": {
            "light": "dark",
            "weather": "light rain",
            "surface": "wet"
          },
          "detection_quality": 0.71
        }
      ]
    },
    "error": null
  }
  ```

---

### 2.3 Plain-English Incident Narration
- **Method**: `GET`
- **Path**: `/api/events/{id}/narrative`
- **Owner**: Lane F (Narration / Integration)
- **Path Parameter**: `id` (e.g. `evt_00417`)
- **Response (`200 OK`)**:
  ```json
  {
    "ok": true,
    "data": {
      "event_id": "evt_00417",
      "narrative": "At 7:47 pm on Thursday, after dark and in light rain, a motorcycle travelling at 47 km/h approached from the north as a car entered from the east against the usual flow of traffic. The two came within 0.8 seconds of collision. This junction has recorded 23 similar conflicts this month, 16 of them in wet conditions after sunset.",
      "generated_at": "2026-08-28T19:47:15Z",
      "cached": true
    },
    "error": null
  }
  ```

---

### 2.4 Scored Road Segments for Risk Map (M11 Map Geometry)
- **Method**: `GET`
- **Path**: `/api/segments`
- **Owner**: Lane D (Server) & Lane C/E (Geometry & Map)
- **Description**: Returns physical road lane segments with GeoJSON coordinate geometry, learned civil speed norms (V85), risk scores, and conflict metrics for Leaflet polyline rendering.
- **Response (`200 OK`)**:
  ```json
  {
    "ok": true,
    "data": {
      "junction_id": "junction_a_evening",
      "location": [13.0106, 74.7943],
      "segments": [
        {
          "segment_id": "seg_0",
          "lane_id": 0,
          "name": "Main Arterial Eastbound",
          "heading_deg": 1.7,
          "geometry": {
            "type": "LineString",
            "coordinates": [
              [74.79430, 13.01060],
              [74.79442, 13.01061],
              [74.79455, 13.01062],
              [74.79480, 13.01063]
            ]
          },
          "centreline_m": [
            [0.0, 0.1],
            [13.25, 0.49],
            [26.5, 0.89],
            [39.75, 1.29],
            [59.62, 1.89]
          ],
          "risk_score": 0.78,
          "risk_level": "high",
          "conflict_count": 14,
          "severe_conflict_count": 5,
          "speed_stats": {
            "v85_kmh": 49.8,
            "mean_kmh": 41.2,
            "sample_size": 62
          },
          "hourly_distribution": [0, 0, 0, 1, 3, 8, 12, 18, 14, 9, 6, 2]
        },
        {
          "segment_id": "seg_1",
          "lane_id": 1,
          "name": "Cross Street Northbound",
          "heading_deg": 90.0,
          "geometry": {
            "type": "LineString",
            "coordinates": [
              [74.79450, 13.01040],
              [74.79450, 13.01055],
              [74.79450, 13.01070],
              [74.79450, 13.01085]
            ]
          },
          "centreline_m": [
            [29.9, -25.0],
            [29.9, -13.96],
            [29.9, -2.92],
            [29.9, 8.12],
            [29.9, 24.68]
          ],
          "risk_score": 0.42,
          "risk_level": "moderate",
          "conflict_count": 7,
          "severe_conflict_count": 2,
          "speed_stats": {
            "v85_kmh": 34.5,
            "mean_kmh": 28.0,
            "sample_size": 38
          },
          "hourly_distribution": [0, 0, 0, 0, 1, 4, 6, 8, 5, 4, 2, 1]
        }
      ]
    },
    "error": null
  }
  ```

---

### 2.5 Pipeline & Edge Health (Live Dashboard Panel)
- **Method**: `GET`
- **Path**: `/api/health`
- **Owner**: Lane D (Server) & Lane A (Edge FPS / Benchmark)
- **Description**: Returns live system telemetry including real measured laptop CPU proxy frame rate, motion gate escalation statistics, buffer state, and PostgreSQL connection.
- **Response (`200 OK`)**:
  ```json
  {
    "ok": true,
    "data": {
      "status": "healthy",
      "pipeline": {
        "fps": 18.4,
        "target_fps": 30.0,
        "cpu_utilization_pct": 34.2,
        "detector_mode": "laptop_cpu_proxy_320",
        "motion_gate_active": true,
        "escalation_rate_pct": 52.0,
        "frames_processed": 1800,
        "dropped_frames": 0
      },
      "buffer": {
        "pending_events": 0,
        "storage": "sqlite",
        "sync_status": "synced",
        "last_flush": "2026-08-30T09:48:10Z"
      },
      "database": {
        "status": "connected",
        "engine": "postgresql",
        "latency_ms": 4.2
      },
      "uptime_seconds": 3840.5,
      "timestamp": "2026-08-30T09:49:52Z"
    },
    "error": null
  }
  ```
