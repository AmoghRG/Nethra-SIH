"""
Unit tests for NETRA M5: Self-Calibrating Road Norms Engine
Verifies all acceptance criteria in PRD Section 7 (M5):
  1. V85 speed computation and sample size recording
  2. Trajectory clustering and lane geometry extraction
  3. Manually reversed test track flagged as 'against flow'
  4. Graceful handling of nulls and schema compliance
"""

import json
from pathlib import Path
import unittest
import numpy as np

from edge.norms.v85_speed import compute_v85, extract_track_speeds
from edge.norms.lane_clustering import extract_lane_clusters
from edge.norms.flow_classifier import classify_track_flow
from edge.norms.norms_engine import run_norms_pipeline


class TestRoadNormsEngine(unittest.TestCase):

    def test_v85_speed_calculation(self):
        """Tests that V85 accurately calculates the 85th percentile speed and sample size."""
        # 100 vehicles with known speed distribution
        speeds = [40.0] * 84 + [60.0] * 16  # 84 cars at 40 km/h, 16 cars at 60 km/h -> 85th percentile is 60.0 km/h
        res = compute_v85(speeds, min_sample_size=100)

        self.assertEqual(res["sample_size"], 100)
        self.assertAlmostEqual(res["speed_85_kmh"], 60.0, places=1)
        self.assertTrue(res["statistically_sound"])

    def test_empty_tracks_v85_fallback(self):
        """Tests that empty or missing track data degrades gracefully without crashing."""
        res = compute_v85([])
        self.assertIsNone(res["speed_85_kmh"])
        self.assertEqual(res["sample_size"], 0)
        self.assertFalse(res["statistically_sound"])

    def test_lane_clustering_and_centrelines(self):
        """Tests that parallel and perpendicular trajectory streams cluster into distinct lanes."""
        tracks = []
        # Lane A: Eastbound (x: 0 -> 40, y: 0)
        for tid in range(10):
            for step in range(10):
                tracks.append({
                    "frame": step, "t": step * 0.1, "track_id": tid, "cls": "car",
                    "bbox": [100, 100, 150, 150], "conf": 0.9,
                    "ground_m": [float(step * 4), 0.0], "v_mps": [10.0, 0.0]
                })

        # Lane B: Northbound (x: 20, y: -20 -> 20)
        for tid in range(10, 20):
            for step in range(10):
                tracks.append({
                    "frame": step, "t": step * 0.1, "track_id": tid, "cls": "car",
                    "bbox": [100, 100, 150, 150], "conf": 0.9,
                    "ground_m": [20.0, float(-20 + step * 4)], "v_mps": [0.0, 10.0]
                })

        lanes, track_lane_map = extract_lane_clusters(tracks, min_samples=3)
        self.assertGreaterEqual(len(lanes), 2)

        headings = [l["heading_deg"] for l in lanes]
        # One lane should be heading ~0 deg (East), one lane ~90 deg (North)
        self.assertTrue(any(abs(h - 0.0) < 15.0 or abs(h - 360.0) < 15.0 for h in headings))
        self.assertTrue(any(abs(h - 90.0) < 15.0 for h in headings))

    def test_reversed_track_flagged_against_flow(self):
        """
        PRD Acceptance Criterion:
        'A manually reversed test track is flagged against flow.'
        """
        # Define a learned lane heading East (heading ~ 0 deg)
        learned_lanes = [{
            "id": 0,
            "centreline_m": [[0.0, 0.0], [20.0, 0.0], [40.0, 0.0]],
            "heading_deg": 0.0,
        }]

        # Normal vehicle moving Eastbound (x: 5 -> 35, y: 0.1)
        normal_track = [
            {"t": 1.0, "ground_m": [5.0, 0.1]},
            {"t": 2.0, "ground_m": [20.0, 0.1]},
            {"t": 3.0, "ground_m": [35.0, 0.1]},
        ]
        res_normal = classify_track_flow(normal_track, learned_lanes)
        self.assertEqual(res_normal["direction"], "normal")
        self.assertEqual(res_normal["matched_lane_id"], 0)

        # Manually reversed track moving Westbound in Eastbound lane (x: 35 -> 5, y: 0.1)
        reversed_track = [
            {"t": 1.0, "ground_m": [35.0, 0.1]},
            {"t": 2.0, "ground_m": [20.0, 0.1]},
            {"t": 3.0, "ground_m": [5.0, 0.1]},
        ]
        res_reversed = classify_track_flow(reversed_track, learned_lanes)
        self.assertEqual(res_reversed["direction"], "against flow")
        self.assertEqual(res_reversed["matched_lane_id"], 0)
        self.assertAlmostEqual(res_reversed["angular_deviation_deg"], 180.0, delta=5.0)

    def test_full_pipeline_schema_conformity(self):
        """Tests that full pipeline produces valid JSON adhering to Section 5.4 schema."""
        tracks_file = Path("fixtures/tracks.sample.jsonl")
        self.assertTrue(tracks_file.exists())

        from edge.norms.norms_engine import load_tracks_jsonl
        tracks = load_tracks_jsonl(tracks_file)
        norms = run_norms_pipeline(tracks)

        # Verify keys
        self.assertIn("speed_85_kmh", norms)
        self.assertIn("sample_size", norms)
        self.assertIn("lanes", norms)
        self.assertIn("signal_cycle_s", norms)

        self.assertIsInstance(norms["speed_85_kmh"], (float, int))
        self.assertIsInstance(norms["sample_size"], int)
        self.assertIsInstance(norms["lanes"], list)
        self.assertTrue(norms["signal_cycle_s"] is None or isinstance(norms["signal_cycle_s"], (float, int)))

        for lane in norms["lanes"]:
            self.assertIn("id", lane)
            self.assertIn("centreline_m", lane)
            self.assertIn("heading_deg", lane)
            self.assertIsInstance(lane["centreline_m"], list)
            self.assertIsInstance(lane["heading_deg"], (float, int))


if __name__ == "__main__":
    unittest.main()
