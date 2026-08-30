# server/scoring/ - risk scoring and road segments

**Owner: D.** Nothing here yet.

Turns stored ConflictEvents into the scored segments behind `GET /api/segments`
and the map E renders.

Use the multiplier model from `server/enrich/`, not a model per condition:

    risk = base_risk(location) x rain_factor x night_factor x peak_factor

and normalise exposure by `detection_quality`, which every edge event carries.
Raw conflict counts fall in rain and darkness because detection degrades, not
because the road got safer.

## First file

`server/scoring/segments.py`
