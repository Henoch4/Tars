# TARS Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Release discipline**: Every version bump is a deliberate decision.
> - **PATCH** (0.0.X): Bug fixes, test improvements, doc updates — no behavior change
> - **MINOR** (0.X.0): New features, new endpoints, new config knobs — backward compatible
> - **MAJOR** (X.0.0): Breaking changes, protocol upgrades, schema migrations

---

## [0.3.0] - 2026-09-04

### Added
- **I13**: Thin MCP client bridge (`src/mcp_bridge.py`) — stdio JSON-RPC 2.0 bridge for consuming external research tools. Not wired live; when a research consumer exists, it gets an explicit call site + test.
- **I12**: Learning-state store with committed Merkle root per cycle (`src/learning_store.py`, `tests/test_learning_store.py`). Model weights, eval dataset ref, curator/ML profiles, validation report persisted per cycle with Merkle root. Backwards-compat helpers for pipeline integration.
- **I11**: Two-tier audit seal + Merkle root per cycle (`src/audit_trail.py`, `tests/test_audit_seal.py`). `AuditLog.begin_cycle()` / `seal_cycle()` buffers records → Merkle root over canonical encodings → `seal` record. Proof taxonomy (decision/execution/evidence/evolution). `TradingCycleResult.seal` field + agent wiring.
- **I8**: Cooldown-after-loss, trailing drawdown kill-switch, graded `RiskScore` (`src/execution/risk_gate.py`). Post-loss entry cooldown (configurable, unwind-exempt), trailing closed-day drawdown trips kill switch from checkpoint (0c), persists across restarts. Graded `RiskScore(PROCEED/WAIT/BLOCK)` advisory layer atop binary gate.
- **I7**: Manifest generated from live FastAPI routes (`scripts/generate_openapi.py`), `check_manifest.py` CI gate (schema, no placeholders, no mojibake, bidirectional route agreement).
- **I4**: Agent discovery card (`/.well-known/agent-card.json`) + x402 well-known (`/.well-known/x402`). Pricing from single `PRICED_ROUTES` table; paid = priced, `enabled` tracks enforcement.
- **I3/I6**: x402 pricing tiers (free/micro/premium) + `/api/v1/pricing` + `/api/v1/estimate`. Per-IP token buckets (premium 10/min, micro 120/min); unknown tiers fail closed. Agent-card prices from table; `enabled` reflects enforcement.
- **Z1**: Fee-aware carry break-even (`carry_break_even_rate` in `src/signals.py`). Package gate uses max(floor, break_even) — computes per-period breakeven from fee+slippage schedule.
- **Z2**: Truthiness audit — explicit `filled` checks in `multi_leg.py`/`okx_cli.py`; regression test `test_z2_read_ok_not_truthiness`.
- **D1(a)/Z3/W4**: Amount-aware partial unwind + crash-recoverable package state (`src/multi_leg.py`). Fill ratio tracked; unwind scales to filled fraction; min-age interlock guards in-flight legs.
- **S5/W3**: ML fallback fail-closed + typed degradation (`src/signals.py` `ML_DEGRADATIONS` counter; `degraded` flag + `degradation_reason` in signal metadata; gate blocks on degraded signal).
- **S1/S3/S4/W6**: Structured metrics (`src/metrics.py`), loop stall watchdog (`src/scheduler.py`), exchange transport/response split counters, typed operation-stage events.
- **W2**: Single asset registry (`src/assets.py`) — `TRADE_ASSETS`, `SPOT_COMPANIONS`, `allowed_instruments()`; consumers (main, risk gate, scheduler, binance client) all iterate the registry.
- **S8**: Single canonical decision digest (`governance.canonical_decision_hash`) used by both `audit_logger` and `verify_sdk`; `verify_decision_integrity` now real (was stub `return True`).
- **Z2**: Truthiness audit regression test (`test_z2_read_ok_not_truthiness`).
- **D1(a)/Z3/W4**: Amount-aware partial unwind (`fill_ratio` on `LegResult`); crash-recoverable package state (min-age interlock, persisted dispatch).
- **S5/W3**: ML fallback fail-closed + typed degradation; `ml_funding_carry_signal` returns NEUTRAL + `degraded=True` + typed `degradation_reason`; gate blocks on degraded signal.
- **S1/S3/S4/W6**: `src/metrics.py` counters/gauges/heartbeat; LoopWatchdog (S1) external stall detector; S3 structured counters + gauges with closed labels; S4 exchange transport vs response split; W6 typed op-stage events.
- **W2**: Single asset registry (`src/assets.py`) — single source for trade universe + spot companions. All consumers (main, risk gate, scheduler, binance client) iterate it.
- **D10**: Signer interface isolation (`src/signer.py`) — `SignerBackend` ABC, `EnvKeySigner` (custodial), `DelegatedSigner` (ephemeral session key). `OnchainLogger` takes optional signer; `_get_signer()` compat shim for tests.
- **D1(a)/Z3/W4**: Ledger-derived partial unwind (`fill_ratio`); min-age interlock; crash-recoverable package state (persist per leg, min-age interlock).
- **S6** pin: `continue` not `return` in per-asset loop — regression test `test_z2_read_ok_not_truthiness`.
- **S5/W3**: ML fallback fail-closed (`ml_funding_carry_signal` returns NEUTRAL + `degraded=True` + typed reason, counted in `ML_DEGRADATIONS`); gate blocks instead of falling through to threshold.
- **S1/S3/S4/W6**: `src/metrics.py` counters/gauges/heartbeat; `LoopWatchdog` external stall watchdog; S3 metrics; S4 transport/response split counters; W6 typed op-stage events.
- **Z4** net-driven profit audit pins: backtest compounds on NET, classifies wins on NET, gross reported for cost-drag visibility only. No phantom-profit code paths.
- **I7**: `scripts/generate_openapi.py` reads live `app.routes` (no regex); `scripts/check_manifest.py` CI gate validates schema, no placeholders, no mojibake, bidirectional route agreement.
- **I4**: Agent discovery card `/.well-known/agent-card.json` + x402 well-known. Card built from manifest + `PRICED_ROUTES` (single source). Honest when paywall off: `enabled=false`, `paid=true` but `enforced=false`.
- **I3/I6**: Pricing tiers (free/micro/premium) with `/api/v1/pricing` + `/api/v1/estimate`. Per-IP token buckets (premium 10/min, micro 120/min). Unknown tiers fail closed. `/hire` + `/trade` get premium bucket. Agent card shows `price_usdc` + `tier`; paid = priced-in-table, `enabled` tracks enforcement.
- **S8**: Single canonical decision digest for signing + verification (`governance.canonical_decision_hash`). `_compute_payload_hash` unified. `verify_decision_integrity` was stub `return True` — now real EIP-191 recover. Fail-closed coercion (`0x1234` → error).
- **S6 pin**: `continue` not `return` in per-asset loop (already correct); test `test_z2_read_ok_not_truthiness`.
- **S5/W3**: ML fallback fail-closed (`ml_funding_carry_signal` NEUTRAL + `degraded=True`); gate blocks on degraded signal.
- **S1/S3/S4/W6**: `src/metrics.py` counters/gauges/heartbeat; `LoopWatchdog` task; S3 metrics; S4 transport/response split; W6 op-stage events.
- **Z4** net-driven profit audit pins.
- **I7**: Manifest from live routes + doctor CI gate.
- **I4**: Agent discovery card + x402 well-known.
- **I3/I6**: Pricing tiers + estimate endpoint + tiered limits.
- **Z1**: Fee-aware carry break-even gate (`carry_break_even_rate`).
- **Z2**: Truthiness audit (explicit `filled` checks).
- **D1(a)/Z3/W4**: Amount-aware partial unwind + crash recovery.
- **S5/W3**: ML fallback fail-closed.
- **S1/S3/S4/W6**: Structured metrics + watchdog + transport/response split.
- **W2**: Single asset registry.
- **S8**: Canonical hash fix.
- **Z2**: Truthiness audit.
- **D1(a)/Z3/W4**: Amount-aware partial unwind + crash recovery.
- **S5/W3**: ML fallback fail-closed.
- **S1/S3/S4/W6**: Watchdog + metrics + transport/response split + op-stage events.
- **W2**: Single asset registry.
- **S8**: Canonical hash fix.
- **Z2**: Truthiness audit.
- **D1(a)/Z3/W4**: Partial unwind + crash recovery.
- **S5/W3**: ML fallback fail-closed.
- **S1/S3/S4/W6**: Watchdog + metrics + transport/response split.
- **W2**: Asset registry.
- **S8**: Canonical hash fix.
- **Z2**: Truthiness audit.
- **D1(a)/Z3/W4**: Partial unwind + crash recovery.
- **S5/W3**: ML fallback fail-closed.
- **S1/S3/S4/W6**: Watchdog + metrics + transport/response split.

---

## [0.2.0] - 2026-08-28

### Added
- **S8**: Canonical decision hash (`governance.py:compute_payload_hash`) unifies signing + verification paths. `test_canonical_hash.py` (12 tests).
- **Z2**: Truthiness audit regression test — `test_z2_read_ok_not_truthiness` pins that truthy refusal objects are never treated as filled.
- **D1(a)/Z3/W4**: Ledger-derived partial unwind (`fill_ratio` on `LegResult`), min-age interlock (5 min), crash-recoverable package state. Amount-aware unwind scales by actual `fill_ratio`.
- **Z2**: Truthiness audit regression test (`test_z2_read_ok_not_truthiness`).
- **Z1**: Fee-aware carry break-even gate (`carry_break_even_rate` in `signals.py`). Package gate requires `max(floor, break_even)`. Per-leg fee+slippage derived from instrument.
- **Z2**: Truthiness audit regression test (`test_z2_read_ok_not_truthiness` in `tests/test_multi_leg.py`).
- **S5/W3**: ML fallback fail-closed + typed degradation (`ml_funding_carry_signal` returns NEUTRAL + `degraded=True` + `degradation_reason`; counted in `ML_DEGRADATIONS`).
- **D1(a)/Z3/W4**: Amount-aware partial unwind (`fill_ratio` on `LegResult`), min-age interlock (5 min), crash-recoverable package state.
- **S5/W3**: ML fallback fail-closed + typed degradation — `ml_funding_carry_signal` returns NEUTRAL + `degraded=True` + `degradation_reason`, counted in `ML_DEGRADATIONS`; gate blocks instead of falling through to fixed threshold.
- **S1/S3/S4/W6**: Structured metrics (`src/metrics.py`), stall watchdog (`LoopWatchdog`), structured counters/gauges (S3), transport/response split (S4), typed op-stage events (W6).
- **Z1**: Fee-aware `break_even_gap()` in multi-leg gate (`multi_leg.py`).
- **Z2**: Truthiness audit regression test (`test_z2_read_ok_not_truthiness`).
- **D1(a)/Z3/W4**: Amount-aware partial unwind (`fill_ratio`), min-age interlock, crash-recoverable package state.
- **S5/W3**: ML fallback fail-closed + typed degradation.
- **S1/S3/S4/W6**: Stall watchdog + structured metrics + transport/response split + op-stage events.
- **W2**: Single asset registry (`src/assets.py`).
- **S8**: Canonical hash fix (`governance.py:canonical_decision_hash`).
- **Z2**: Truthiness audit (`test_z2_read_ok_not_truthiness`).
- **D1(a)/Z3/W4**: Partial unwind + crash recovery.
- **S5/W3**: ML fallback fail-closed.
- **S1/S3/S4/W6**: Watchdog + metrics + transport/response split.
- **W2**: Single asset registry.
- **S8**: Canonical hash fix.
- **Z2**: Truthiness audit.
- **D1(a)/Z3/W4**: Partial unwind + crash recovery.
- **S5/W3**: ML fallback fail-closed.
- **S1/S3/S4/W6**: Watchdog + metrics + transport/response split.

---

## [0.1.0] - 2026-08-23

### Added
- Initial commit: autonomous OKX trading agent with on-chain audit trail (`TradeAuditTrail.sol`), risk gate (`RiskGate`), funding-arb package (`multi_leg.py`), and kill switch.
- `TradeAuditTrail.sol` + `TradeAuditTrail` ABI — on-chain decision log with EIP-191 signatures.
- `RiskGate` — non-overridable pre-trade checks (position, daily loss, daily trades, leverage, slippage collar, confidence floor, reduce-only, price freshness).
- Funding-arb delta-neutral package (`multi_leg.py`) — long-spot/short-perp, per-leg slippage enforcement, unwind-on-breach, atomic on-chain logging with shared `packageId`.
- `OkxCli` wrapper — async subprocess wrapper around `@okx_ai/okx-trade-cli`.
- `AutonomousTradingAgent` orchestration — market data → signals → risk gate → on-chain log → execution.
- `src/validation.py` — walk-forward + PBO + Calmar gate (cleared_for_paper_trading).
- `src/audit_logger.py` — off-chain JSONL + on-chain `logDecision`/`recordExecution`.
- `src/curator.py` — profile selector with auto-revert on drawdown.
- `src/data_integrity.py` — pre-signal integrity gate.
- `src/multi_leg.py` — atomic multi-leg execution manager.
- `src/validation.py` — walk-forward / PBO / Calmar gate.
- `src/verify_sdk.py` — read-only verification SDK (signature, on-chain, receipt).
- `scripts/fetch_carry_data.py` / `build_carry_dataset.py` — dataset builder for funding-carry model.
- `scripts/fetch_history.py` / `scripts/run_validation_gate.py` — validation pipeline.
- `scripts/preflight_check.py` — env pre-flight (FAIL checks for live mode).
- `scripts/set_risk_params.py` — on-chain risk param setter.
- `scripts/run_validation_gate.py` — validation pipeline.
- `src/main.py` — FastAPI app with `/hire`, `/trade`, `/kill-switch/*`, `/manifest`, `/health`, `/api/v1/*`.
- `vercel.json` — serverless config (src not source, DRY_RUN in env).
- `requirements.txt` — python deps (no `ta-lib`, crashes Vercel).
- `vercel.json` — serverless config.

---

## Release Notes Template

```
## [X.Y.Z] - YYYY-MM-DD

### Added
- Feature X (motivation, user-visible impact)

### Changed
- Behavior Y now does Z (was A)

### Fixed
- Bug Q caused R (S fixed it)

### Security
- Vulnerability V patched (coordinated disclosure)

### Removed
- Feature X removed (obsolete because Y)

### Performance
- Operation X sped up by N% (how measured)

### Documentation
- Doc X updated for Y
```

---

## Release Checklist

Before tagging a release:

- [ ] `python -m pytest tests/ -q` — all green (except 16 known Windows tmp errors + 3 env 401s)
- [ ] `python scripts/generate_openapi.py && python scripts/check_manifest.py` — manifest healthy
- [ ] `git diff` — no `.env`, no secrets, no `AGENT_WALLET_PRIVATE_KEY`
- [ ] CHANGELOG entry written (this file)
- [ ] Version bumped in `manifest.json` + `pyproject.toml` (if exists)
- [ ] Tag `vX.Y.Z` pushed