"""
NETRA M9 — Human Ground-Truth Check & Verification Engine
Evaluates NETRA's conflict detections against blind human labels.
Computes and reports raw counts (Caught N of M, Invented K, Missed J),
Precision, Recall, F1 Score, and severity alignment tables per PRD Section 7 (M9) & S4.
"""

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple


def parse_timestamp_seconds(time_val: Any, base_time: Optional[datetime] = None) -> float:
    """
    Parses various timestamp representations into relative video elapsed seconds.
    Supports float seconds, integer seconds, or ISO 8601 strings.
    """
    if isinstance(time_val, (int, float)):
        return float(time_val)
    if isinstance(time_val, str):
        # Try direct float conversion
        try:
            return float(time_val)
        except ValueError:
            pass

        # Try ISO 8601 parsing
        try:
            dt = datetime.fromisoformat(time_val.replace("Z", "+00:00"))
            if base_time is None:
                return 0.0
            return (dt - base_time).total_seconds()
        except Exception:
            return 0.0
    return 0.0


def load_ground_truth(gt_path: Path) -> List[Dict[str, Any]]:
    """Loads human ground-truth labels from CSV."""
    labels = []
    if not gt_path.exists():
        raise FileNotFoundError(f"Ground truth file not found at {gt_path}")

    with open(gt_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, start=1):
            try:
                t_start = float(row["t_start_s"])
                t_end = float(row["t_end_s"])
                labels.append({
                    "id": f"gt_{idx:03d}",
                    "t_start_s": t_start,
                    "t_end_s": t_end,
                    "severity": row["severity"].strip().lower(),
                    "vehicle_a": row.get("vehicle_a", "").strip().lower(),
                    "vehicle_b": row.get("vehicle_b", "").strip().lower(),
                    "notes": row.get("notes", "").strip(),
                })
            except Exception as e:
                print(f"Skipping malformed GT row {idx}: {e}", file=sys.stderr)
    return labels


def load_events(events_path: Path) -> List[Dict[str, Any]]:
    """Loads ConflictEvents from JSON array or JSONL file."""
    events = []
    if not events_path.exists():
        raise FileNotFoundError(f"Events file not found at {events_path}")

    with open(events_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if content.startswith("["):
            # JSON array format
            raw_events = json.loads(content)
        else:
            # JSONL format
            raw_events = [json.loads(line) for line in content.splitlines() if line.strip()]

    # Establish base timestamp for relative offset calculation if ISO timestamps are used
    iso_times = []
    for evt in raw_events:
        t_str = evt.get("time")
        if isinstance(t_str, str):
            try:
                iso_times.append(datetime.fromisoformat(t_str.replace("Z", "+00:00")))
            except Exception:
                pass

    base_time = min(iso_times) if iso_times else None

    for idx, evt in enumerate(raw_events, start=1):
        # Determine event timestamp in seconds
        t_sec = None
        if "t_s" in evt:
            t_sec = float(evt["t_s"])
        elif "time_s" in evt:
            t_sec = float(evt["time_s"])
        elif "t" in evt:
            t_sec = float(evt["t"])
        elif "time" in evt:
            t_sec = parse_timestamp_seconds(evt["time"], base_time)
        else:
            t_sec = float(idx * 60.0)  # fallback

        va = evt.get("vehicle_a", {})
        vb = evt.get("vehicle_b", {})
        va_type = va.get("type", "") if isinstance(va, dict) else str(va)
        vb_type = vb.get("type", "") if isinstance(vb, dict) else str(vb)

        events.append({
            "event_id": evt.get("event_id", f"evt_{idx:03d}"),
            "t_sec": round(t_sec, 2),
            "severity": evt.get("severity", "conflict").lower(),
            "ttc_s": evt.get("ttc_s"),
            "pet_s": evt.get("pet_s"),
            "vehicle_a": va_type,
            "vehicle_b": vb_type,
            "type": evt.get("type", "conflict"),
            "raw": evt,
        })
    return events


def evaluate_ground_truth(
    gt_labels: List[Dict[str, Any]],
    detected_events: List[Dict[str, Any]],
    tolerance_s: float = 2.0,
) -> Dict[str, Any]:
    """
    Matches detected events against ground-truth labels and computes benchmark metrics.

    Args:
        gt_labels: List of ground-truth conflict records.
        detected_events: List of system-detected ConflictEvent records.
        tolerance_s: Time matching window around [t_start - tol, t_end + tol].

    Returns:
        Evaluation report dictionary with raw counts, Precision, Recall, and F1.
    """
    total_gt = len(gt_labels)
    total_det = len(detected_events)

    matched_gt_ids = set()
    matched_event_ids = set()
    matches = []

    # Match each GT label to nearest candidate detection
    for gt in gt_labels:
        g_start = gt["t_start_s"] - tolerance_s
        g_end = gt["t_end_s"] + tolerance_s

        best_evt = None
        min_dist = float("inf")

        for evt in detected_events:
            t_e = evt["t_sec"]
            if g_start <= t_e <= g_end:
                dist = abs(t_e - (gt["t_start_s"] + gt["t_end_s"]) / 2.0)
                if dist < min_dist:
                    min_dist = dist
                    best_evt = evt

        if best_evt is not None:
            matched_gt_ids.add(gt["id"])
            matched_event_ids.add(best_evt["event_id"])
            matches.append({
                "gt": gt,
                "event": best_evt,
                "severity_match": gt["severity"] == best_evt["severity"],
            })

    tp = len(matched_gt_ids)
    fn = total_gt - tp  # Missed real near-misses (J)
    fp = total_det - len(matched_event_ids)  # Invented false alarms (K)

    recall = (tp / total_gt) if total_gt > 0 else 0.0
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    # Severity-specific breakdowns
    gt_severe = [g for g in gt_labels if g["severity"] == "severe"]
    gt_conflict = [g for g in gt_labels if g["severity"] == "conflict"]

    tp_severe = sum(1 for m in matches if m["gt"]["severity"] == "severe")
    tp_conflict = sum(1 for m in matches if m["gt"]["severity"] == "conflict")

    recall_severe = (tp_severe / len(gt_severe)) if gt_severe else 0.0
    recall_conflict = (tp_conflict / len(gt_conflict)) if gt_conflict else 0.0

    return {
        "summary": {
            "total_ground_truth_M": total_gt,
            "caught_true_positives_N": tp,
            "false_positives_K": fp,
            "missed_false_negatives_J": fn,
            "total_detections": total_det,
            "recall": round(recall, 3),
            "precision": round(precision, 3),
            "f1_score": round(f1, 3),
        },
        "severity_breakdown": {
            "severe": {
                "total_gt": len(gt_severe),
                "caught": tp_severe,
                "recall": round(recall_severe, 3),
            },
            "conflict": {
                "total_gt": len(gt_conflict),
                "caught": tp_conflict,
                "recall": round(recall_conflict, 3),
            },
        },
        "matches": matches,
        "unmatched_gt": [g for g in gt_labels if g["id"] not in matched_gt_ids],
        "unmatched_events": [e for e in detected_events if e["event_id"] not in matched_event_ids],
    }


def format_markdown_report(report: Dict[str, Any]) -> str:
    """Generates an engineer-readable markdown accuracy report."""
    s = report["summary"]
    sev = report["severity_breakdown"]

    md = []
    md.append("# NETRA Ground-Truth Verification Report (Module M9)")
    md.append(f"**Empirical Success Criterion S4 Check**: Caught **{s['caught_true_positives_N']} of {s['total_ground_truth_M']}** human-labelled near-misses with **{s['false_positives_K']} false alarms**.\n")

    md.append("## 1. Overall Accuracy Metrics")
    md.append("| Metric | Count / Score | Civil Meaning |")
    md.append("|---|---|---|")
    md.append(f"| **Ground-Truth Conflicts ($M$)** | `{s['total_ground_truth_M']}` | Total human-verified conflict encounters |")
    md.append(f"| **Detected True Positives ($N$)** | `{s['caught_true_positives_N']}` | Correctly identified near-misses |")
    md.append(f"| **Missed Conflicts ($J$)** | `{s['missed_false_negatives_J']}` | False negatives |")
    md.append(f"| **Spurious Inventions ($K$)** | `{s['false_positives_K']}` | False positives |")
    md.append(f"| **Recall (Sensitivity)** | **`{s['recall']*100:.1f}%`** | Proportion of real danger caught |")
    md.append(f"| **Precision** | **`{s['precision']*100:.1f}%`** | Proportion of alerts that are real |")
    md.append(f"| **F1 Score** | **`{s['f1_score']:.3f}`** | Harmonic mean accuracy |")

    md.append("\n## 2. Breakdown by Severity")
    md.append("| Severity Level | Total GT | Caught | Recall (%) |")
    md.append("|---|---|---|---|")
    md.append(f"| **Severe (TTC < 0.8s)** | {sev['severe']['total_gt']} | {sev['severe']['caught']} | **{sev['severe']['recall']*100:.1f}%** |")
    md.append(f"| **Conflict (TTC < 1.5s)** | {sev['conflict']['total_gt']} | {sev['conflict']['caught']} | **{sev['conflict']['recall']*100:.1f}%** |")

    return "\n".join(md)


def main():
    parser = argparse.ArgumentParser(description="NETRA M9 Ground-Truth Evaluator")
    parser.add_argument("--gt", type=Path, default=Path("eval/groundtruth/labels.csv"), help="Path to ground truth labels.csv")
    parser.add_argument("--events", type=Path, default=Path("fixtures/events.sample.json"), help="Path to events.json or events.jsonl")
    parser.add_argument("--tolerance", type=float, default=2.0, help="Matching time tolerance window in seconds (default: 2.0s)")
    parser.add_argument("--output", type=Path, default=Path("eval/groundtruth/eval_results.json"), help="Path to save evaluation JSON results")
    args = parser.parse_args()

    gt_labels = load_ground_truth(args.gt)
    events = load_events(args.events)

    report = evaluate_ground_truth(gt_labels, events, tolerance_s=args.tolerance)

    # Output JSON
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # Print summary markdown
    print(format_markdown_report(report))
    print(f"\nSaved structured evaluation report -> {args.output}")


if __name__ == "__main__":
    main()
