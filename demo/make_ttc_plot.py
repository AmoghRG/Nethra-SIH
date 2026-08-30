"""TTC against time for the conflicting pair. Owner F's directory.

    python -m demo.make_ttc_plot --events out/demo/events.jsonl \\
        --trace out/demo/trace.jsonl --calib fixtures/calibration.demo_clip.json \\
        --out out/demo/ttc_plot.png

Why this is worth a chart rather than a number. The overlay flashes a TTC past
the audience once; a plot shows the whole approach - the gap closing, TTC
falling through 1.5 s and then 0.8 s, and the moment of minimum. It also makes
the thresholds visible as lines rather than as claims, which is the difference
between a judge believing the measurement and taking it on trust.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from demo._common import (
    PROVISIONAL_NOTE,
    calibration_is_provisional,
    load_events,
    pick_event,
    trace_for_pair,
)
from edge.common.jsonl import load_jsonl


def main(argv: list[str] | None = None) -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    p = argparse.ArgumentParser(description="TTC-over-time plot (demo artefact)")
    p.add_argument("--events", required=True)
    p.add_argument("--trace", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--calib", default=None)
    p.add_argument("--event-id", default=None)
    p.add_argument("--severe-s", type=float, default=0.8)
    p.add_argument("--conflict-s", type=float, default=1.5)
    args = p.parse_args(argv)

    event = pick_event(load_events(args.events), args.event_id)
    rows = trace_for_pair(load_jsonl(args.trace), *event["track_ids"])
    if not rows:
        raise SystemExit("no trace rows for tracks {}".format(event["track_ids"]))

    t = [r["t"] for r in rows]
    ttc = [r["ttc_s"] for r in rows]
    gap = [r["gap_m"] for r in rows]

    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [3, 2]})

    ax.axhspan(0, args.severe_s, color="#e04b4b", alpha=0.13)
    ax.axhspan(args.severe_s, args.conflict_s, color="#f0a63c", alpha=0.13)
    ax.axhline(args.conflict_s, color="#f0a63c", lw=1.4, ls="--",
               label="conflict threshold {:.1f} s".format(args.conflict_s))
    ax.axhline(args.severe_s, color="#e04b4b", lw=1.4, ls="--",
               label="severe threshold {:.1f} s".format(args.severe_s))

    # Gaps in the line where TTC is infinite are meaningful: the pair was not
    # on a converging course at all in those frames. Plot them as breaks
    # rather than interpolating through, which would invent an approach.
    ax.plot(t, ttc, color="#2b6cb0", lw=1.6, alpha=0.55, label="TTC (raw)")

    # Mark which frames the suppression rules removed, and which survived.
    # Without this the curve looks like a two-second emergency; with it, the
    # picture is honest - most of that curve is a pair the rules correctly
    # refused to call a conflict, and the event rests on the few frames that
    # got through.
    rule_colours = {
        "velocity_noise": "#a0aec0",
        "lane_splitting": "#9f7aea",
        "stopped_vehicles": "#4fd1c5",
        "speed_sanity": "#ed8936",
        "validity_region": "#718096",
    }
    for rule, colour in rule_colours.items():
        xs = [r["t"] for r in rows if r["suppressed_by"] == rule and r["ttc_s"] is not None]
        ys = [r["ttc_s"] for r in rows if r["suppressed_by"] == rule and r["ttc_s"] is not None]
        if xs:
            ax.plot(xs, ys, ls="none", marker="x", ms=6, color=colour,
                    label="suppressed: {} ({})".format(rule, len(xs)))
    kept_x = [r["t"] for r in rows if r["suppressed_by"] is None and r["ttc_s"] is not None]
    kept_y = [r["ttc_s"] for r in rows if r["suppressed_by"] is None and r["ttc_s"] is not None]
    if kept_x:
        ax.plot(kept_x, kept_y, ls="none", marker="o", ms=8, mfc="#2b6cb0",
                mec="white", mew=1.5, label="kept ({})".format(len(kept_x)), zorder=4)
    ax.plot(event["t_video_s"], event["ttc_s"], marker="*", ms=20, color="#c0392b",
            ls="none", label="minimum {:.2f} s".format(event["ttc_s"]), zorder=5)
    ax.annotate("{:.2f} s".format(event["ttc_s"]),
                (event["t_video_s"], event["ttc_s"]),
                textcoords="offset points", xytext=(12, 12),
                fontsize=11, color="#c0392b", fontweight="bold")

    ax.set_ylabel("time to collision (s)")
    ax.set_ylim(bottom=0)
    ax.set_title("{}  -  {} between {} and {}".format(
        event["event_id"], event["type"],
        event["vehicle_a"]["type"], event["vehicle_b"]["type"]))
    ax.legend(loc="upper right", fontsize=8, ncol=2)
    ax.grid(alpha=0.25)

    ax2.plot(t, gap, color="#2f855a", lw=2.0, marker="o", ms=3)
    ax2.axvline(event["t_video_s"], color="#c0392b", lw=1.2, ls=":")
    ax2.set_ylabel("centre-to-centre gap (m)")
    ax2.set_xlabel("time from start of video (s)")
    ax2.grid(alpha=0.25)

    provisional = args.calib and calibration_is_provisional(args.calib)
    footer = "tracks {} | speeds {:.0f} and {:.0f} km/h".format(
        event["track_ids"], event["vehicle_a"]["speed_kmh"], event["vehicle_b"]["speed_kmh"])
    if provisional:
        footer += "\n" + PROVISIONAL_NOTE
    fig.text(0.01, 0.01, footer, fontsize=8.5,
             color="#b7791f" if provisional else "#555555")

    fig.tight_layout(rect=(0, 0.045, 1, 1))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=150)
    print("wrote {}".format(args.out))
    print("event {}  min TTC {:.2f} s at t={:.2f}s  ({} trace rows)".format(
        event["event_id"], event["ttc_s"], event["t_video_s"], len(rows)))
    if provisional:
        print(PROVISIONAL_NOTE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
