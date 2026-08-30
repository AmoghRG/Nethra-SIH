# tracks.jsonl (PRD 5.2)

One JSON object per line, per track per frame.

```json
{"frame": 1240, "t": 41.33, "track_id": 87, "cls": "motorcycle",
 "bbox": [310, 442, 356, 501], "conf": 0.81,
 "ground_m": [14.2, 31.7], "v_mps": [8.1, -2.4]}
```

Producers: M2 writes `bbox`, `conf`, `cls`. M1's homography is applied to
produce `ground_m` and `v_mps`.

Rule: a consumer may assume `ground_m` is present only if `calibration.json`
was supplied.
