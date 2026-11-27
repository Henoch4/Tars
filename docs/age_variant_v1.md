# `age_variant_v1` matched-pair experiment

This is a paper-only experiment for the main memecoin satellite. It does not
modify the production core and can never promote itself to live trading.

- Baseline: the current `scream_filter` exactly as deployed.
- Variant: the same decision snapshot plus `age_s >= 1,800` seconds.
- Notional: `$25` per hypothetical position.
- Maximum open variant positions: `3`.
- Required sample: `500` completed paper trades.
- Comparison: same mint and same discovery timestamp.

Every record stores the complete causal `TokenSnapshot`, timestamp, baseline
decision, and variant decision. Missing age rejects the variant; it never gets
treated as zero or as a pass. No live promotion is permitted for this version.

`src/meme_age_ledger.py` provides the paper accounting layer: fixed `$25`
notional, three-position cap, tiered adverse slippage plus constant-product
impact, 1.25% per-leg fees, and explicit failed-sell losses. It is separate
from all live execution code.

`src/meme_age_runner.py` now feeds each causal snapshot into two isolated paper
books. Young candidates can open only the baseline book; mature candidates can
open both books. Exit prices, timestamps, and failure reasons are applied to
both books identically for a matched comparison.

The runner's `mark()` method applies the existing deterministic satellite exit
rules to both books: rug sentinel, time box, peak drawdown/profit ratchet, dead
tape, and tax trap. A triggered exit closes both matched positions with the
same observed price and liquidity; failed sells remain explicit losses.
