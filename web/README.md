# web/ - M11 risk map dashboard

**Owner: E. Priority P0.** Nothing here yet.

The surface the judge actually looks at.

## Required (P0)

- Leaflet map, road segments coloured by risk
- Click a segment: conflict count, time-of-day chart, speed distribution with
  the learned 85th-percentile limit marked
- Event list with severity and timestamp
- Video clip playback for a detected conflict

## Required (P1)

- **Condition filters** - the same map under "rain, after dark" versus "dry,
  daytime". This is the visual proof of the condition-aware claim; without it
  M6 is invisible.
- Plain-English write-up displayed alongside each event

## Acceptance

- Loads and renders **from fixtures with the backend stopped**. No blank screen
  on API failure: degrade to cached data with a visible "offline" badge.
- Every panel handles null. A missing signal cycle hides one card; it does not
  white-screen the app.
- Tested at the actual projector resolution before hour 22.

## Build against the contract, not against D

`contracts/api.md` has the endpoints and the response envelope
`{"ok": true, "data": {}, "error": null}`. Swap from fixtures to the live API
at hour 14.

`fixtures/events.edge.sample.json` has 6 real events in the exact shape the
pipeline produces. `conditions` is null and `direction` is absent in edge
output - D adds both on ingest - so starting here exercises your null handling
for free.

## First file

`web/index.html`
