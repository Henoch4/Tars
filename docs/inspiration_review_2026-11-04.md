# Inspiration and future-proposition review — 2026-11-04

## Scope and reading note

This review covers the first-party strategy documents, all `docs/*stealables*`
notes, `PATH_FORWARD.md`, `OPEN_DECISIONS.md`, `DESIGN-external-vault.md`, the
current build plan, and the README/index material for the vendored model
repositories under `model-inspiration/`. Those vendored repositories contain
thousands of upstream implementation and reference pages; they are source
material, not Tarstrade product requirements. Their relevant patterns are
captured here without copying their frameworks or claiming that a README is
validation evidence.

## The consistent design thesis

Tarstrade's strongest product is not “an AI that trades.” It is a constrained,
auditable research-to-execution system:

1. data integrity and evidence quality;
2. a strategy signal with explicit scope;
3. deterministic risk and sizing;
4. durable order intent and execution state;
5. audit-before-trade and operator-visible outcomes.

The inspirations repeatedly support this shape. They do not justify adding a
larger model, more agents, more venues, or more symbols before the SOL paper
cohort and execution lifecycle are proven.

## Start now — highest-value local work

### 1. Replace file claims with a transactional intent ledger (P0)

The current claim store prevents a duplicate `client_oid` on one host, but it
does not yet provide a durable intent state machine or fencing token. Build a
small SQLite ledger on the persistent VPS path:

- `intent_id`, `client_oid`, account/book, owner token, created/expiry times;
- states `CREATED → AUTHORIZED → SUBMITTING → UNKNOWN → ACKED/FAILED`;
- unique key on logical order identity;
- transactionally reserve trade/volume quota with the intent;
- preserve `UNKNOWN` after timeout and require venue lookup before retry;
- allow verified reduce-only recovery while blocking new entries on corruption.

This is the direct implementation of `build_plan` Stage B and the approved
ownership proposition. It is more valuable than another signal experiment.

### 2. Finish quantity-first multi-leg recovery (P0)

`OPEN_DECISIONS.md` approved the quantity-first design. Extend `LegResult` with
requested, filled, remaining, and average-fill quantities. Package locking must
require the economic hedge invariant, not merely an exchange status. Unwind the
actual filled exposure to zero, including partial, late, duplicate, and rounded
fills. Keep packages disabled in the scheduler until this is complete.

### 3. Close defensive-input gaps (P0)

Implement the documented semantics before expanding any strategy:

- empty enabled-signal policy denies all; absent policy means default policy;
- unavailable funding/position data is not a valid zero;
- reject stale, future, non-finite, malformed, and unknown exposure inputs;
- preserve an explicit safe-unwind path when entries are halted.

These are small, testable changes with a direct safety payoff.

## Start after the P0 execution work

### 4. Make paper and agent FPZ lifecycle identical (P1)

Record one settled funding sample per venue period, persist history across
restart, and implement entry, hold, opposite-signal close, maximum hold,
reduce-only exit, partial-close recovery, funding accrual, fees, and actual
closed-trade PnL. The paperz cohort must measure the real lifecycle, not an
estimated completion path.

### 5. Build the operator evidence surface (P1)

Expose owner identity, effective mode, runtime readiness, last heartbeat, block
reason, exposure, audit readiness, claim/intent state, and configuration digest.
Separate API health from scheduler/paperz health. Add a capability manifest so a
feature is only described as available when implementation, fixture, and tests
exist.

## Future propositions worth keeping — but gated

- **Vault/product:** implement share accounting, fee policy, legal review, and
  first-loss tranche design only after the strategy has a validated quarter.
- **Advisory layer:** a Claude-Ads/Sendo-style recommendation and explanation
  layer can be built outside the order path. It must never place orders or
  weaken the deterministic gate.
- **Venue-aware slippage:** borrow the Hummingbot/fairswap idea later—slippage
  as a function of size, liquidity, venue, and inventory—not as a framework
  dependency.
- **Research recorder:** borrow Qlib/Freqtrade experiment provenance, rolling
  evaluation, and reproducible datasets for paper evidence, not their full
  runtime frameworks.
- **Hazard/volatility models:** Chronos/TimesFM/NeuralForecast/Darts remain
  ablation candidates only after cheap tree/hazard baselines fail across
  regimes and the data/label store is durable.
- **Factor proposal automation:** RD-Agent-style proposals may suggest factors,
  but every proposal must pass the existing purged walk-forward/PBO gate.

## Explicitly do not start now

- no live FPZ flip or capital deployment;
- no BTC/ETH symbol expansion from the SOL result;
- no retuning on the paper cohort;
- no directional ML resurrection (mean reversion, momentum, or prior carry
  model remain falsified);
- no memecoin restart, tokenized stocks, venue expansion, or heavy foundation
  model dependency;
- no full Freqtrade/Hummingbot/Qlib framework import.

## Recommended build order

`SQLite intent ledger → quantity-first multi-leg recovery → defensive input
semantics → FPZ lifecycle parity → operator evidence/capability manifest →
vault/advisory/venue-aware research propositions.`

The paperz loop continues independently. Its 30-trade, positive-net-carry,
no-anomaly gate remains a strategy-evidence requirement; passing it cannot
substitute for the execution and custody controls above.
