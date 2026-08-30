# contracts/ — owner F

Frozen at hour 2. Changing anything here requires the integration owner's
sign-off and a broadcast to the team (PRD Section 5).

Every module codes against these, not against another person's implementation.

| File | PRD | Produced by | Consumed by |
|---|---|---|---|
| `calibration.schema.json` | 5.1 | M1 | M3 |
| `tracks.md` | 5.2 | M2 (+M1 homography) | M3, M5, M9, M10 |
| `conflict_event.schema.json` | 5.3 | M3 | M7, M11, M9 |
| `norms.schema.json` | 5.4 | M5 | M11 |
| `http_api.md` | 5.5 | M7 (owner D) | M11 (owner E), M9 |
