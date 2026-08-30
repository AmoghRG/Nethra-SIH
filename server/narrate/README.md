# server/narrate/ - M8 plain-English incident writer

**Owner: F. Priority P1.** Nothing here yet.

Turns a structured record into prose an engineer reads without decoding JSON.

> At 7:47 pm on Thursday, after dark and in light rain, a motorcycle travelling
> at 47 km/h approached from the north as a car entered from the east against
> the usual flow of traffic. The two came within 0.8 seconds of collision. This
> junction has recorded 23 similar conflicts this month, 16 of them in wet
> conditions after sunset.

## Three rules that make this safe rather than dangerous

1. **Server-side only.** A Raspberry Pi cannot run a useful language model, and
   this must never sit on the critical path of conflict detection.
2. **It describes only what is in the record.** The model receives the JSON and
   is instructed to describe these fields, invent nothing, speculate about
   nothing, and assign blame to nobody. It is a translator from numbers to
   sentences, not an analyst.
3. **The structured record stays the source of truth.** The prose is a view of
   the data, never a replacement. If the two ever disagree, the numbers win.

## Acceptance

- Output 80 words or fewer.
- **Field-presence assertion:** for 10 sample events, every number appearing in
  the prose is traceable to a field in the source record. Any hallucinated
  figure is a build failure.
- Null fields are omitted from the prose, not guessed at. Edge events have
  `conditions` null and no `direction` until D enriches them, so a narrator
  working from raw edge output must handle both absences.

## Cut line, and do this deliberately rather than as a fallback

**Pre-generate narratives for the demo events at hour 20 and cache them to
disk.** That removes live API latency and network dependency from the demo
entirely. Live generation is a nice-to-have; cached output is indistinguishable
to a judge and cannot fail on stage.

## Explicitly not built

Free-text questions turned into database queries. It looks impressive and it
silently produces wrong answers. In a system about road safety, that trade is
not worth making.

## Input available now

`fixtures/events.edge.sample.json`. Write and test the prompt against it before
a single real event exists.

## First file

`server/narrate/writer.py`
