"""
NETRA M9 — Ground-Truth Annotation CLI
Tool for Person C to quickly record, validate, and manage blind human ground-truth labels.
Outputs: eval/groundtruth/labels.csv
"""

import argparse
import csv
from pathlib import Path
import sys
from typing import List, Optional

LABELS_CSV_PATH = Path(__file__).resolve().parent / "labels.csv"
CSV_HEADER = ["t_start_s", "t_end_s", "severity", "vehicle_a", "vehicle_b", "notes"]
VALID_SEVERITIES = ["conflict", "severe"]
VALID_VEHICLES = ["car", "motorcycle", "truck", "bus", "auto", "pedestrian"]


def init_labels_file(file_path: Path) -> None:
    """Initializes a new labels.csv with header if it doesn't exist."""
    if not file_path.exists():
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
        print(f"Initialized new ground-truth labels file at {file_path}")


def add_label(
    file_path: Path,
    t_start: float,
    t_end: float,
    severity: str,
    veh_a: str,
    veh_b: str,
    notes: str,
) -> bool:
    """Validates and appends a single ground-truth label entry."""
    # Validation
    if t_start < 0 or t_end <= t_start:
        print(f"Error: Invalid timestamps t_start={t_start}, t_end={t_end}. Must have 0 <= t_start < t_end", file=sys.stderr)
        return False

    severity = severity.strip().lower()
    if severity not in VALID_SEVERITIES:
        print(f"Error: Invalid severity '{severity}'. Must be one of {VALID_SEVERITIES}", file=sys.stderr)
        return False

    veh_a = veh_a.strip().lower()
    veh_b = veh_b.strip().lower()
    if veh_a not in VALID_VEHICLES or veh_b not in VALID_VEHICLES:
        print(f"Error: Invalid vehicle class. Valid classes are {VALID_VEHICLES}", file=sys.stderr)
        return False

    init_labels_file(file_path)

    with open(file_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([round(t_start, 2), round(t_end, 2), severity, veh_a, veh_b, notes.strip()])

    print(f"Added label: [{round(t_start, 1)}s - {round(t_end, 1)}s] ({severity}) {veh_a} vs {veh_b} - '{notes}'")
    return True


def list_and_validate_labels(file_path: Path) -> int:
    """Reads, validates, and prints summary table of all recorded labels."""
    if not file_path.exists():
        print(f"No labels file found at {file_path}")
        return 0

    rows = []
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, start=1):
            try:
                t_start = float(row["t_start_s"])
                t_end = float(row["t_end_s"])
                sev = row["severity"].strip().lower()
                va = row["vehicle_a"].strip().lower()
                vb = row["vehicle_b"].strip().lower()
                notes = row.get("notes", "")

                if t_start >= t_end:
                    print(f"Warning on row {idx}: t_start >= t_end ({t_start} >= {t_end})")
                if sev not in VALID_SEVERITIES:
                    print(f"Warning on row {idx}: unknown severity '{sev}'")
                if va not in VALID_VEHICLES or vb not in VALID_VEHICLES:
                    print(f"Warning on row {idx}: unknown vehicle type in '{va}' or '{vb}'")

                rows.append({
                    "idx": idx,
                    "t_start": t_start,
                    "t_end": t_end,
                    "duration": round(t_end - t_start, 1),
                    "severity": sev,
                    "vehicles": f"{va} vs {vb}",
                    "notes": notes,
                })
            except Exception as e:
                print(f"Error parsing row {idx}: {e}")

    # Display table
    print(f"\n--- Ground Truth Labels ({len(rows)} total records in {file_path.name}) ---")
    print(f"{'#':<3} | {'Interval (s)':<15} | {'Dur (s)':<7} | {'Severity':<10} | {'Vehicles':<22} | {'Notes'}")
    print("-" * 85)
    severe_count = 0
    conflict_count = 0

    for r in rows:
        interval_str = f"{r['t_start']:.1f}s - {r['t_end']:.1f}s"
        print(f"{r['idx']:<3} | {interval_str:<15} | {r['duration']:<7.1f} | {r['severity']:<10} | {r['vehicles']:<22} | {r['notes']}")
        if r["severity"] == "severe":
            severe_count += 1
        elif r["severity"] == "conflict":
            conflict_count += 1

    print("-" * 85)
    print(f"Summary: Total: {len(rows)} | Severe: {severe_count} | Conflict: {conflict_count}\n")
    return len(rows)


def interactive_mode(file_path: Path) -> None:
    """Interactive loop to quickly enter labels one after another while watching video."""
    print("=== NETRA Interactive Ground-Truth Annotator ===")
    print("Type 'q' or press Ctrl+C to quit.\n")
    init_labels_file(file_path)

    while True:
        try:
            t_start_inp = input("Start timestamp t_start (seconds, e.g. 42.5): ").strip()
            if t_start_inp.lower() in ["q", "quit", "exit"]:
                break
            t_start = float(t_start_inp)

            t_end_inp = input("End timestamp t_end (seconds, e.g. 45.0): ").strip()
            t_end = float(t_end_inp)

            sev = input("Severity [conflict/severe] (default: conflict): ").strip().lower() or "conflict"
            va = input("Vehicle A [car/motorcycle/auto/truck/bus/pedestrian] (default: motorcycle): ").strip().lower() or "motorcycle"
            vb = input("Vehicle B [car/motorcycle/auto/truck/bus/pedestrian] (default: car): ").strip().lower() or "car"
            notes = input("Notes / description: ").strip()

            add_label(file_path, t_start, t_end, sev, va, vb, notes)
            print("-" * 40)
        except (KeyboardInterrupt, EOFError):
            print("\nExiting annotator.")
            break
        except ValueError as ve:
            print(f"Invalid input: {ve}. Please enter numeric values for timestamps.\n")


def main():
    parser = argparse.ArgumentParser(description="NETRA M9 Ground-Truth Labeling CLI")
    parser.add_argument("--file", type=Path, default=LABELS_CSV_PATH, help="Path to labels.csv")
    parser.add_argument("--list", action="store_true", help="List and validate all recorded labels")
    parser.add_argument("--interactive", action="store_true", help="Launch interactive entry loop")
    parser.add_argument("--add", action="store_true", help="Add a single label via CLI arguments")
    parser.add_argument("--t_start", type=float, help="Conflict start timestamp in seconds")
    parser.add_argument("--t_end", type=float, help="Conflict end timestamp in seconds")
    parser.add_argument("--severity", type=str, default="conflict", help="Severity: conflict | severe")
    parser.add_argument("--veh_a", type=str, default="motorcycle", help="Vehicle A class")
    parser.add_argument("--veh_b", type=str, default="car", help="Vehicle B class")
    parser.add_argument("--notes", type=str, default="", help="Description notes")

    args = parser.parse_args()

    if args.add:
        if args.t_start is None or args.t_end is None:
            print("Error: --add requires both --t_start and --t_end", file=sys.stderr)
            sys.exit(1)
        add_label(args.file, args.t_start, args.t_end, args.severity, args.veh_a, args.veh_b, args.notes)
    elif args.interactive:
        interactive_mode(args.file)
    else:
        list_and_validate_labels(args.file)


if __name__ == "__main__":
    main()
