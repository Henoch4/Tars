# GTA Synthetic Round-Trip Policy Review — 2026-11-20

The isolated replay returned a successful unsigned buy/sell simulation for the
candidate `BsbsB3WLq7vbY5En3MBCAsTrcwaCxNKY2Mp5pGuLpump`.

| Measure | Result |
|---|---:|
| Input | 1,000,000 lamports |
| Buy output | 357,240,043 raw tokens |
| Sell output | 976,272 lamports |
| Aggregate observed loss | 2.3728% |
| Configured historical hard ceiling | 15% |
| Policy comparison | within ceiling |

The 2.3728% is an aggregate simulation observation. The current artifact does
not expose a trustworthy decomposition into swap fee, transfer fee, price
impact, priority fee, and route slippage. Those components therefore remain
`unknown`, not zero. The result may corroborate sellability, but cannot satisfy
the authoritative transfer-tax gate by itself.

The replay artifact is attached by reference:
`docs/gta_synthetic_roundtrip_2026-11-20.json`.

Decision: retain `simulation_only`; do not promote the candidate. The sixth
shadow remains disabled until finalized real-mint calibration, external
corroboration, LP/bundle/authority evidence, and independent tax/honeypot
outcomes are complete and conflict-free.
