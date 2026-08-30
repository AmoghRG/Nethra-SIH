"""
NETRA Synthetic Data & Mock Generator
Generates realistic fixtures for Person C (M5, M9, M10) and the entire NETRA pipeline.
Outputs:
  - fixtures/tracks.sample.jsonl (PRD Section 5.2 format)
  - fixtures/events.sample.json (PRD Section 5.3 format)
  - fixtures/groundtruth.sample.csv (PRD Section 7 M9 format)
"""

import json
import math
import random
from pathlib import Path

random.seed(42)

FIXTURES_DIR = Path(__file__).resolve().parent
TRACKS_FILE = FIXTURES_DIR / "tracks.sample.jsonl"
EVENTS_FILE = FIXTURES_DIR / "events.sample.json"
GT_FILE = FIXTURES_DIR / "groundtruth.sample.csv"

CLASSES = ["car", "motorcycle", "truck", "bus", "auto", "pedestrian"]


def generate_tracks(num_tracks=60, total_frames=1800, fps=30):
    """
    Generates realistic trajectory tracks on a synthetic 4-way / junction road geometry.
    - Lane 0: Eastbound (x: 0 -> 60m, y: ~0m, heading ~0 deg)
    - Lane 1: Westbound (x: 60 -> 0m, y: ~4m, heading ~180 deg)
    - Lane 2: Northbound (x: ~30m, y: -30 -> 30m, heading ~90 deg)
    - Wrong-way tracks: Eastbound lane, but heading ~180 deg
    - Conflict encounters: Crossing vehicles at (30m, 0m)
    - Perspective overlap (for M10 baseline failure case): 2 cars in same lane 25m apart with 2D bbox overlap
    """
    tracks_records = []
    
    # Pre-generate vehicle track metadata
    # 1. Main Eastbound stream (Lane 0)
    for i in range(25):
        track_id = 100 + i
        cls = random.choices(["car", "motorcycle", "auto", "truck", "bus"], weights=[0.4, 0.35, 0.15, 0.05, 0.05])[0]
        # Speed in km/h (log-normal or normal distribution around 42 km/h, 85th percentile around 52 km/h)
        speed_kmh = max(20.0, random.gauss(42.0, 9.0))
        speed_mps = speed_kmh / 3.6
        start_frame = int(i * 65 + random.uniform(0, 30))
        duration_frames = int((60.0 / speed_mps) * fps)
        
        y_offset = random.gauss(0.0, 0.4)
        for f_idx in range(duration_frames):
            frame = start_frame + f_idx
            if frame >= total_frames:
                break
            t = round(frame / fps, 2)
            progress = f_idx / duration_frames
            x_m = round(progress * 60.0, 2)
            y_m = round(y_offset + 0.03 * x_m, 2)
            vx = round(speed_mps, 2)
            vy = round(0.03 * speed_mps, 2)
            
            # Simple synthetic camera projection for 2D bbox [ymin, xmin, ymax, xmax]
            # Camera at top looking down-angle
            pixel_x = int(200 + x_m * 12 + random.uniform(-1, 1))
            pixel_y = int(500 + y_m * 10 + random.uniform(-1, 1))
            w = 50 if cls in ["car", "auto"] else (25 if cls == "motorcycle" else 75)
            h = 40 if cls in ["car", "auto"] else (30 if cls == "motorcycle" else 65)
            bbox = [pixel_y - h, pixel_x - w//2, pixel_y, pixel_x + w//2]
            conf = round(random.uniform(0.75, 0.95), 2)
            
            tracks_records.append({
                "frame": frame,
                "t": t,
                "track_id": track_id,
                "cls": cls,
                "bbox": bbox,
                "conf": conf,
                "ground_m": [x_m, y_m],
                "v_mps": [vx, vy]
            })

    # 2. Main Westbound stream (Lane 1)
    for i in range(20):
        track_id = 200 + i
        cls = random.choices(["car", "motorcycle", "auto", "bus"], weights=[0.45, 0.3, 0.15, 0.1])[0]
        speed_kmh = max(22.0, random.gauss(45.0, 8.0))
        speed_mps = speed_kmh / 3.6
        start_frame = int(i * 80 + random.uniform(10, 40))
        duration_frames = int((60.0 / speed_mps) * fps)
        
        y_offset = 4.0 + random.gauss(0.0, 0.3)
        for f_idx in range(duration_frames):
            frame = start_frame + f_idx
            if frame >= total_frames:
                break
            t = round(frame / fps, 2)
            progress = f_idx / duration_frames
            x_m = round(60.0 - progress * 60.0, 2)
            y_m = round(y_offset - 0.02 * (60.0 - x_m), 2)
            vx = round(-speed_mps, 2)
            vy = round(-0.02 * speed_mps, 2)
            
            pixel_x = int(200 + x_m * 12)
            pixel_y = int(450 + y_m * 10)
            w = 50 if cls in ["car", "auto"] else (25 if cls == "motorcycle" else 75)
            h = 40 if cls in ["car", "auto"] else (30 if cls == "motorcycle" else 65)
            bbox = [pixel_y - h, pixel_x - w//2, pixel_y, pixel_x + w//2]
            conf = round(random.uniform(0.78, 0.96), 2)
            
            tracks_records.append({
                "frame": frame,
                "t": t,
                "track_id": track_id,
                "cls": cls,
                "bbox": bbox,
                "conf": conf,
                "ground_m": [x_m, y_m],
                "v_mps": [vx, vy]
            })

    # 3. Northbound Cross Street (Lane 2)
    for i in range(15):
        track_id = 300 + i
        cls = random.choices(["car", "motorcycle", "auto"], weights=[0.4, 0.4, 0.2])[0]
        speed_kmh = max(18.0, random.gauss(32.0, 6.0))
        speed_mps = speed_kmh / 3.6
        start_frame = int(i * 110 + random.uniform(5, 50))
        duration_frames = int((50.0 / speed_mps) * fps)
        
        x_offset = 30.0 + random.gauss(0.0, 0.4)
        for f_idx in range(duration_frames):
            frame = start_frame + f_idx
            if frame >= total_frames:
                break
            t = round(frame / fps, 2)
            progress = f_idx / duration_frames
            x_m = round(x_offset, 2)
            y_m = round(-25.0 + progress * 50.0, 2)
            vx = round(0.0, 2)
            vy = round(speed_mps, 2)
            
            pixel_x = int(200 + x_m * 12)
            pixel_y = int(300 + y_m * 14)
            w = 45 if cls in ["car", "auto"] else 25
            h = 35 if cls in ["car", "auto"] else 25
            bbox = [pixel_y - h, pixel_x - w//2, pixel_y, pixel_x + w//2]
            conf = round(random.uniform(0.76, 0.94), 2)
            
            tracks_records.append({
                "frame": frame,
                "t": t,
                "track_id": track_id,
                "cls": cls,
                "bbox": bbox,
                "conf": conf,
                "ground_m": [x_m, y_m],
                "v_mps": [vx, vy]
            })

    # 4. Wrong-way vehicle (Motorcycle in Eastbound lane going Westbound!)
    wrong_way_tracks = [
        {"id": 401, "start_frame": 400, "cls": "motorcycle", "speed_kmh": 36.0, "y_m": 0.2},
        {"id": 402, "start_frame": 1100, "cls": "auto", "speed_kmh": 28.0, "y_m": 0.5}
    ]
    for ww in wrong_way_tracks:
        speed_mps = ww["speed_kmh"] / 3.6
        duration_frames = int((40.0 / speed_mps) * fps)
        for f_idx in range(duration_frames):
            frame = ww["start_frame"] + f_idx
            if frame >= total_frames:
                break
            t = round(frame / fps, 2)
            progress = f_idx / duration_frames
            x_m = round(50.0 - progress * 40.0, 2)
            y_m = round(ww["y_m"], 2)
            vx = round(-speed_mps, 2)
            vy = 0.0
            pixel_x = int(200 + x_m * 12)
            pixel_y = int(500 + y_m * 10)
            bbox = [pixel_y - 25, pixel_x - 12, pixel_y, pixel_x + 12]
            tracks_records.append({
                "frame": frame,
                "t": t,
                "track_id": ww["id"],
                "cls": ww["cls"],
                "bbox": bbox,
                "conf": 0.86,
                "ground_m": [x_m, y_m],
                "v_mps": [vx, vy]
            })

    # 5. Converging Near-Miss Conflict (Crossing at t ~ 75s and t ~ 230s)
    # Track 501 (Eastbound) & Track 502 (Northbound) arrive at (30m, 0m) almost simultaneously
    conflict_scenarios = [
        {"id_a": 501, "cls_a": "motorcycle", "v_kmh_a": 48.0, "id_b": 502, "cls_b": "car", "v_kmh_b": 35.0, "time_s": 75.0},
        {"id_a": 503, "cls_a": "bus", "v_kmh_a": 40.0, "id_b": 504, "cls_b": "motorcycle", "v_kmh_b": 35.0, "time_s": 230.0}
    ]
    for cs in conflict_scenarios:
        t_center = cs["time_s"]
        frame_center = int(t_center * fps)
        v_a_mps = cs["v_kmh_a"] / 3.6
        v_b_mps = cs["v_kmh_b"] / 3.6
        
        # 3 seconds before to 3 seconds after
        for delta_f in range(-90, 90):
            frame = frame_center + delta_f
            if frame < 0 or frame >= total_frames:
                continue
            dt = delta_f / fps
            t = round(frame / fps, 2)
            
            # Vehicle A: Eastbound approaching (30, 0)
            x_a = round(30.0 + dt * v_a_mps, 2)
            y_a = 0.0
            px_a = int(200 + x_a * 12)
            py_a = int(500 + y_a * 10)
            tracks_records.append({
                "frame": frame, "t": t, "track_id": cs["id_a"], "cls": cs["cls_a"],
                "bbox": [py_a - 25, px_a - 15, py_a, px_a + 15], "conf": 0.89,
                "ground_m": [x_a, y_a], "v_mps": [round(v_a_mps, 2), 0.0]
            })
            
            # Vehicle B: Northbound approaching (30, 0)
            x_b = 30.0
            # Arrives 0.7s after Vehicle A -> near-miss!
            y_b = round((dt - 0.7) * v_b_mps, 2)
            px_b = int(200 + x_b * 12)
            py_b = int(300 + y_b * 14)
            tracks_records.append({
                "frame": frame, "t": t, "track_id": cs["id_b"], "cls": cs["cls_b"],
                "bbox": [py_b - 35, px_b - 20, py_b, px_b + 20], "conf": 0.91,
                "ground_m": [x_b, y_b], "v_mps": [0.0, round(v_b_mps, 2)]
            })

    # Sort tracks by frame then track_id
    tracks_records.sort(key=lambda r: (r["frame"], r["track_id"]))
    
    # Save to JSONL
    with open(TRACKS_FILE, "w", encoding="utf-8") as f:
        for r in tracks_records:
            f.write(json.dumps(r) + "\n")
            
    print(f"Generated {len(tracks_records)} track detections across {total_frames} frames -> {TRACKS_FILE}")
    return tracks_records


if __name__ == "__main__":
    generate_tracks()
