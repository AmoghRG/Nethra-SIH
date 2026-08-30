"""
Unit tests for NETRA M9: Ground-Truth Verification Evaluator
Verifies all acceptance criteria in PRD Section 7 (M9) & Criterion S4:
  1. Accurate True Positive, False Positive, and False Negative tallying
  2. Precision, Recall, and F1 Score calculation
  3. Severe vs Conflict breakdown
  4. Temporal tolerance window matching
"""

import json
from pathlib import Path
import unittest

from eval.groundtruth.evaluator import evaluate_ground_truth, load_events, load_ground_truth


class TestGroundTruthEvaluator(unittest.TestCase):

    def setUp(self):
        # 3 mock ground truth labels
        self.mock_gt = [
            {"id": "gt_001", "t_start_s": 10.0, "t_end_s": 12.0, "severity": "severe", "vehicle_a": "motorcycle", "vehicle_b": "car"},
            {"id": "gt_002", "t_start_s": 30.0, "t_end_s": 32.0, "severity": "conflict", "vehicle_a": "car", "vehicle_b": "auto"},
            {"id": "gt_003", "t_start_s": 50.0, "t_end_s": 52.0, "severity": "severe", "vehicle_a": "bus", "vehicle_b": "truck"},
        ]

    def test_perfect_detection_match(self):
        """Tests 100% precision and recall when all events match GT within tolerance."""
        detected = [
            {"event_id": "evt_1", "t_sec": 11.0, "severity": "severe", "vehicle_a": "motorcycle", "vehicle_b": "car"},
            {"event_id": "evt_2", "t_sec": 31.0, "severity": "conflict", "vehicle_a": "car", "vehicle_b": "auto"},
            {"event_id": "evt_3", "t_sec": 51.0, "severity": "severe", "vehicle_a": "bus", "vehicle_b": "truck"},
        ]

        report = evaluate_ground_truth(self.mock_gt, detected, tolerance_s=2.0)
        s = report["summary"]

        self.assertEqual(s["total_ground_truth_M"], 3)
        self.assertEqual(s["caught_true_positives_N"], 3)
        self.assertEqual(s["false_positives_K"], 0)
        self.assertEqual(s["missed_false_negatives_J"], 0)
        self.assertEqual(s["recall"], 1.0)
        self.assertEqual(s["precision"], 1.0)
        self.assertEqual(s["f1_score"], 1.0)

    def test_misses_and_false_alarms(self):
        """Tests calculation of raw counts when system misses 1 conflict and invents 2 false alarms."""
        detected = [
            {"event_id": "evt_1", "t_sec": 11.0, "severity": "severe"},  # Matches gt_001
            # gt_002 is missed
            {"event_id": "evt_3", "t_sec": 51.0, "severity": "severe"},  # Matches gt_003
            {"event_id": "evt_4", "t_sec": 80.0, "severity": "conflict"},  # False Alarm 1
            {"event_id": "evt_5", "t_sec": 95.0, "severity": "severe"},    # False Alarm 2
        ]

        report = evaluate_ground_truth(self.mock_gt, detected, tolerance_s=2.0)
        s = report["summary"]

        self.assertEqual(s["total_ground_truth_M"], 3)
        self.assertEqual(s["caught_true_positives_N"], 2)
        self.assertEqual(s["missed_false_negatives_J"], 1)
        self.assertEqual(s["false_positives_K"], 2)

        self.assertAlmostEqual(s["recall"], 2 / 3, places=2)
        self.assertAlmostEqual(s["precision"], 2 / 4, places=2)

    def test_severity_breakdown(self):
        """Tests that severe vs conflict recall rates are partitioned accurately."""
        detected = [
            {"event_id": "evt_1", "t_sec": 11.0, "severity": "severe"},   # Matches severe gt_001
            {"event_id": "evt_3", "t_sec": 51.0, "severity": "severe"},   # Matches severe gt_003
            # gt_002 (conflict) missed
        ]

        report = evaluate_ground_truth(self.mock_gt, detected, tolerance_s=2.0)
        sev = report["severity_breakdown"]

        self.assertEqual(sev["severe"]["total_gt"], 2)
        self.assertEqual(sev["severe"]["caught"], 2)
        self.assertEqual(sev["severe"]["recall"], 1.0)

        self.assertEqual(sev["conflict"]["total_gt"], 1)
        self.assertEqual(sev["conflict"]["caught"], 0)
        self.assertEqual(sev["conflict"]["recall"], 0.0)


if __name__ == "__main__":
    unittest.main()
