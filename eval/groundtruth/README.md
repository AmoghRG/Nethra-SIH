# eval/groundtruth/ - M9 human ground-truth check

**Owner: C. Priority P0 - this is the evidence.** Start at hour 2.

"We detected 200 conflicts" is not evidence unless something confirms those
were real. With the rider tier dropped, this is the primary validation, and it
is the single thing most likely to separate this project from the field. Most
competing projects will show detections. Very few will show a measured error
rate.

## Method

A person watches 30 minutes of the demo footage and marks every genuine
near-miss by hand, **before seeing any system output**. That ordering is the
difference between validation and confirmation bias, and it cannot be recovered
afterwards.

## Format

`labels.csv`, header already committed:

    t_start_s,t_end_s,severity,vehicle_a,vehicle_b,notes

`t_start_s` lines up against `t_video_s` on every ConflictEvent. That field
exists (amendment A3) specifically so this comparison does not become manual.

## Acceptance

- **30 minutes** labelled. Cut line is 15; below that the counts stop being
  meaningful.
- Labelling done **blind**.
- Results reported as **raw counts, not just percentages**: caught N of M,
  invented K false positives, missed J.
- A borderline-case policy written down **before** labelling starts, so the
  labeller stays consistent with themselves. Put it in `policy.md` here.

## The tuning trap - agree this with B at hour 14

Do not tune thresholds and report on the same data. Split the labelled footage:

- **First half - tuning set.** B adjusts thresholds and suppression freely.
- **Second half - held out.** Touched once, at hour 18, and that is the number
  reported.

`edge/conflicts/run_conflicts.py --split first-half|second-half` enforces the
split in the tool rather than by memory. A slightly worse honest figure beats a
better one that cannot be defended.

## Why this starts at hour 2

It is the only P0 task a non-coding team member can own, it runs fully parallel
to development, and it takes wall-clock time that cannot be compressed later.
