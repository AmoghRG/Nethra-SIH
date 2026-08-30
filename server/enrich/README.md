# server/enrich/ - M6 weather and time layer

**Owner: D. Priority P1.** Nothing here yet.

## What it is for

Danger is not a property of a place. It is a property of a place under
conditions. A junction can be fine in dry daylight and lethal in wet darkness.

**Runs on the server, never the edge.** The edge sends a timestamp and a
location and writes `conditions: null`; you attach the rest. That keeps the
edge simple and removes any roadside API dependency. If `conditions` ever
arrives from the edge already populated, that is a bug in the edge - tell B.

Attach to every event: hour of day, weekday or weekend, daylight/dusk/dark,
peak or off-peak, rain and intensity, fog, wet or dry surface, temperature.

## Two problems this module must handle

These are as valuable to present as the feature itself.

1. **Detection degrades in rain and darkness**, so raw conflict counts drop in
   bad weather for entirely the wrong reason. Ignore it and you conclude rain
   is safe, which is nonsense. Every edge event already carries
   `detection_quality` (mean detector confidence across both tracks over the
   encounter) for exactly this. Normalise conflict rates by it.

2. **Splitting data across condition buckets leaves each bucket thin.** Do not
   build a model per condition. Build one base risk per location and apply
   multipliers:

       risk = base_risk(location) x rain_factor x night_factor x peak_factor

   Fewer parameters to estimate, far more stable on limited data.

## Acceptance

- Light state from `astral`, which is **offline**. No network required.
- Weather from a cached lookup table committed to the repo. A live API is a
  bonus path, never the demo path.
- If weather lookup fails, the event still stores light and time, and `weather`
  is null.

## Cut line

Ship light and time only. `astral` works offline and gives the day/dark split,
which carries most of the story.

## First file

`server/enrich/conditions.py`
