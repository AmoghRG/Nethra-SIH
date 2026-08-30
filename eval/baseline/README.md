# eval/baseline/ - M10 head-to-head comparison

**Owner: C. Priority P1.** Nothing here yet.

Turns "ours is better" into something a judge can see.

## The argument

Existing open-source projects judge closeness by how much two bounding boxes
overlap on screen. That is broken in two directions: a car 30 metres behind
another overlaps heavily on screen with zero risk, and two genuinely close
vehicles at the far end of the frame barely overlap at all. It also only fires
*after* the boxes overlap, where ours fires 1-2 seconds before.

## Implementation

The IoU-proximity method in perhaps thirty lines, running beside ours on the
same clip, both scored against the same human labels. Input is
`tracks_px.jsonl` - pixels, deliberately, because pixel overlap is the whole
point of the baseline.

## Acceptance

- Both methods scored against `labels.csv` with identical criteria.
- Output is a single table: method, recall, false positives, mean lead time
  before the encounter.
- **At least one clip** where the IoU baseline misses a conflict our method
  catches, cut and ready to play.

## Cut line

Cut the table, keep the single clip. One clip showing the failure mode is worth
more demo time than a table of numbers.

## First file

`eval/baseline/iou_baseline.py`
