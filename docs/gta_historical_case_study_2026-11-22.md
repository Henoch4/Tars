# GTA Satellite Historical Case Study — 22 Nov 2026

## Scope

The GTA shadow data is retained on the VPS under `/opt/tars/data/gta_event/`.
The latest consolidated enrichment milestone is `enrichment_report_v4.json`.
Raw observations remain in `shadow_observations.jsonl` and the diagnostic
milestones.

## Results

| Metric | Result |
|---|---:|
| Latest enrichment observations | 1,007 |
| Complete safety payloads | 0 |
| Candidates allowed | 0 |
| Overall completeness | 0% |
| Helius success | 70 |
| Creator history available | 650 |
| Jupiter audit available | 650 |
| Jupiter round-trip evidence | 363 |
| LP safety evidence | 275 |
| Token-2022 extension observations | 70 |
| Bundle walks incomplete | 84 |
| Bundle walks not sampled | 923 |
| Transfer-tax evidence | 0 authoritative |
| Honeypot evidence missing | 644 |

## Interpretation

The GTA experiment did not fail because the runner could not trade. It failed
closed because the post-hype market produced incomplete safety evidence. The
most decisive blockers were transfer-tax evidence, honeypot evidence, and
complete bundle coverage. The data therefore supports parking GTA as a
historical case study rather than reopening it as a live strategy.

## What the data is useful for

- Measure how quickly narrative tokens lose safety-data coverage after a hype
  event.
- Compare rejection reasons against the core memecoin satellite.
- Test whether stricter filters improve paper outcomes in a future regime
  variant.
- Preserve the GTA window as a benchmark for future catalyst overlays.

No live orders, wallet changes, or shadow promotion resulted from this data.
