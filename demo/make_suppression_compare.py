"""What each suppression rule actually removes. Owner F's directory.

    python -m demo.make_suppression_compare --tracks out/demo/tracks_m.jsonl \\
        --calib fixtures/calibration.demo_clip.json --out out/demo/suppression.png

Runs the conflict engine once with every rule on, once with all of them off,
and once per rule with that rule alone disabled. The middle column is the one
that matters: it is the number of events the pipeline would have reported
without that rule, so the difference is what the rule is worth, measured
rather than asserted.

Parent PRD 6.4 asks for the rules to be individually toggleable so their effect
can be shown. This is that, as a chart.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from edge.calibration.homography import load_calibration
from edge.common.config import load_config
from edge.common.jsonl import load_jsonl
from edge.conflicts.engine import ConflictEngine
from edge.conflicts.suppression import ALL_RULES


def run(tracks, calib, overrides):
    engine = ConflictEngine(load_config(overrides=overrides), calib)
    events = engine.run(tracks)
    return {
        "events": len(events),
        "severe": sum(1 for e in events if e.severity == "severe"),
        "readings": engine.stats.conflict_readings,
        "counts": dict(engine.suppression.counts),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Suppression before/after (demo artefact)")
    p.add_argument("--tracks", required=True, help="tracks_m.jsonl")
    p.add_argument("--calib", required=True)
    p.add_argument("--out", default=None, help="chart PNG; omit for the table only")
    args = p.parse_args(argv)

    calib = load_calibration(args.calib)
    tracks = load_jsonl(args.tracks)

    base = run(tracks, calib, [])
    none = run(tracks, calib, ["suppression.{}=false".format(r) for r in ALL_RULES])

    print("{:<22} {:>8} {:>8} {:>10} {:>26}".format(
        "configuration", "events", "severe", "readings", "extra events without it"))
    print("-" * 78)
    print("{:<22} {:>8} {:>8} {:>10} {:>26}".format(
        "all rules on", base["events"], base["severe"], base["readings"], "-"))

    per_rule = {}
    for rule in ALL_RULES:
        r = run(tracks, calib, ["suppression.{}=false".format(rule)])
        per_rule[rule] = r
        print("{:<22} {:>8} {:>8} {:>10} {:>26}".format(
            "without " + rule, r["events"], r["severe"], r["readings"],
            "{:+d}".format(r["events"] - base["events"])))
    print("{:<22} {:>8} {:>8} {:>10} {:>26}".format(
        "all rules OFF", none["events"], none["severe"], none["readings"],
        "{:+d}".format(none["events"] - base["events"])))

    print()
    print("pair-frames removed by each rule, with everything on:")
    for rule in ALL_RULES:
        n = base["counts"].get(rule, 0)
        print("  {:<20} {:>7}{}".format(
            rule, n, "" if n else "   (nothing on this clip)"))

    idle = [r for r in ALL_RULES if base["counts"].get(r, 0) == 0]
    if idle:
        print()
        print("Rules that removed nothing here are not dead - this clip simply does")
        print("not contain what they are for. Saying so is better than implying the")
        print("contrast is weak because the rules are.")

    if args.out:
        _chart(args.out, base, none, per_rule)
    return 0


def _chart(out, base, none, per_rule):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = ["all rules\non"] + ["without\n" + r.replace("_", "\n") for r in per_rule] + ["all rules\nOFF"]
    values = [base["events"]] + [v["events"] for v in per_rule.values()] + [none["events"]]
    colours = ["#2f855a"] + ["#a0aec0"] * len(per_rule) + ["#c53030"]
    for i, (rule, v) in enumerate(per_rule.items(), start=1):
        if v["events"] > base["events"]:
            colours[i] = "#dd6b20"

    fig, ax = plt.subplots(figsize=(max(9, 1.5 * len(labels)), 5.5))
    bars = ax.bar(labels, values, color=colours)
    # Log scale: the all-off bar is two orders of magnitude above the rest, and
    # on a linear axis it flattens every individual rule into the baseline.
    ax.set_yscale("log")
    ax.set_ylim(top=max(values) * 3)
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, v * 1.12, str(v),
                ha="center", fontsize=11, fontweight="bold")
    ax.axhline(base["events"], color="#2f855a", ls="--", lw=1.2,
               label="with every rule on ({})".format(base["events"]))
    ax.set_ylabel("conflict events reported (log scale)")
    ax.set_title("What each suppression rule removes")
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    print("\nwrote {}".format(out))


if __name__ == "__main__":
    raise SystemExit(main())
