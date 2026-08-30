"""
Unit tests for NETRA M10: Baseline IoU & Comparative Evaluation Harness
Verifies all acceptance criteria in PRD Section 7 (M10):
  1. 2D Bounding Box IoU calculation correctness
  2. Proximity expansion IoU calculation
  3. Baseline event emission
  4. Comparative scoring table and lead-time analysis
"""

import json
from pathlib import Path
import unittest

from eval.baseline.baseline_iou import compute_2d_iou, compute_expanded_proximity_iou, run_baseline_iou_detector
from eval.baseline.compare_harness import compute_warning_lead_times, run_comparative_benchmark


class TestBaselineComparison(unittest.TestCase):

    def test_2d_iou_disjoint_and_overlapping(self):
        """Tests standard 2D Intersection-over-Union geometry."""
        # Disjoint boxes
        box_a = [0, 0, 10, 10]
        box_b = [20, 20, 30, 30]
        self.assertEqual(compute_2d_iou(box_a, box_b), 0.0)

        # Identical boxes (100% overlap)
        self.assertAlmostEqual(compute_2d_iou(box_a, box_a), 1.0, places=3)

        # 50% horizontal overlap
        box_c = [0, 5, 10, 15]
        # Inter = 10 * 5 = 50. Union = 100 + 100 - 50 = 150. IoU = 50/150 = 0.333
        self.assertAlmostEqual(compute_2d_iou(box_a, box_c), 1 / 3, places=3)

    def test_expanded_proximity_iou(self):
        """Tests that expanded proximity detects near-collisions before actual pixel intersection."""
        box_a = [0, 0, 10, 10]
        box_near = [0, 11, 10, 21]  # 1 pixel gap -> direct IoU is 0.0

        direct_iou = compute_2d_iou(box_a, box_near)
        expanded_iou = compute_expanded_proximity_iou(box_a, box_near, expansion_factor=0.3)

        self.assertEqual(direct_iou, 0.0)
        self.assertGreater(expanded_iou, 0.0)

    def test_baseline_detector_on_tracks(self):
        """Tests that baseline detector extracts encounters from track frames."""
        mock_tracks = [
            {"frame": 1, "t": 0.033, "track_id": 1, "cls": "car", "bbox": [100, 100, 150, 150], "conf": 0.9},
            {"frame": 1, "t": 0.033, "track_id": 2, "cls": "car", "bbox": [110, 110, 160, 160], "conf": 0.9},
            {"frame": 2, "t": 0.066, "track_id": 1, "cls": "car", "bbox": [102, 102, 152, 152], "conf": 0.9},
            {"frame": 2, "t": 0.066, "track_id": 2, "cls": "car", "bbox": [112, 112, 162, 162], "conf": 0.9},
            {"frame": 3, "t": 0.100, "track_id": 1, "cls": "car", "bbox": [104, 104, 154, 154], "conf": 0.9},
            {"frame": 3, "t": 0.100, "track_id": 2, "cls": "car", "bbox": [114, 114, 164, 164], "conf": 0.9},
        ]

        events = run_baseline_iou_detector(mock_tracks, iou_threshold=0.05, min_consecutive_frames=2)
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0]["track_ids"], [1, 2])

    def test_comparative_benchmark_pipeline(self):
        """Tests full comparative benchmarking against ground-truth fixtures."""
        gt_path = Path("fixtures/groundtruth.sample.csv")
        ttc_path = Path("fixtures/events.sample.json")
        tracks_path = Path("fixtures/tracks.sample.jsonl")

        self.assertTrue(gt_path.exists())
        self.assertTrue(ttc_path.exists())
        self.assertTrue(tracks_path.exists())

        res = run_comparative_benchmark(gt_path, ttc_path, tracks_path=tracks_path)

        self.assertIn("netra", res)
        self.assertIn("baseline_iou", res)
        self.assertIn("recall", res["netra"])
        self.assertIn("mean_lead_time_s", res["netra"])
        self.assertGreater(res["netra"]["recall"], 0.5)


if __name__ == "__main__":
    unittest.main()
