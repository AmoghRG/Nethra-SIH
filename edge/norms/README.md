# edge/norms/ - M5 self-calibrating road norms

**Owner: C. Priority P1.** Nothing here yet; this is C's to build.

## What it is for

To say a vehicle is speeding you need a speed limit; to say wrong-lane you need
a lane map. In India that data mostly does not exist in machine-readable form.
So the road teaches us its own rules.

| Norm | Method |
|---|---|
| Speed limit | The speed 85 of 100 vehicles stay under - genuinely how engineers set limits |
| Lane positions | Cluster the trajectories; where paths bunch, that is a lane |
| Lane direction | Whichever way nearly everyone goes is correct; anyone else is against the flow |
| Signal cycle | Vehicles cross the stop line in bunches; the bunches reveal the cycle |

## Input, available now

`fixtures/tracks_m.sample.jsonl` - ground-plane tracks in metres, produced by
owner B. Schema: `contracts/tracks_m.schema.json`. You do not need to wait for
the demo video or for the detector.

    from edge.common.jsonl import load_jsonl
    rows = load_jsonl("fixtures/tracks_m.sample.jsonl")

## Output

`norms.json`, per `contracts/norms.schema.json`. **null is a valid value for
any field** and consumers must degrade gracefully: a missing signal cycle hides
one dashboard card, it does not white-screen the page.

## First file

`edge/norms/speed_85.py` - the 85th-percentile speed over completed tracks,
storing `sample_size` alongside the value.

Cut line runs signal cycle first, then lane clustering, then lane direction.
**Keep the 85th percentile.** It is the cheapest to compute and the easiest to
defend, because it is standard traffic-engineering practice.

## Note for owner B's benefit

`vehicle_a.direction` and `vehicle_b.direction` are absent from edge-emitted
ConflictEvents precisely because they need your lane directions. D applies them
on ingest once you produce them.
