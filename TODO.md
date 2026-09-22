# Tarstrade — Comprehensive TODO / Build Order

**Generated:** 2026-09-21 from full repo sweep (all docs, source, contracts, scripts, tests).
**This is the single source of truth for what to build, in build order.**

---

## Legend

| Priority | Meaning |
|----------|---------|
| **P0** | Blocking / safety / currently broken — must fix now |
| **P1** | Required for next phase — gates live capital or production |
| **P2** | Important but not blocking — quality, observability, hardening |
| **P3** | Nice-to-have / future feature — gated behind P0/P1 completion |
| **P4** | Research / exploration — no code yet, documented proposals |

---

## Phase 0 — Immediate (P0): Fix What's Broken

These are currently broken, crashed, or actively blocking the system.

### 0.1 — `src/signals.py` crashes on import (`NameError: DBCCurveType`)
- **Status:** RESOLVED — `DBCCurveType` exists in `src/execution/risk_gate.py` and imports correctly; no crash.
- **Verification:** `import src.agent` works; `python -m pytest tests/ -q` runs (615 passed)
- **Source:** `docs/panta-integration-report.md:69`

### 0.2 — Resolve all failing tests (16 pre-existing Windows tmp errors + 3 env 401s)
- **Status:** RESOLVED — all 615 tests pass. The 16 "errors" were `PermissionError` on `C:\Users\Henoch\AppData\Local\Temp\pytest-of-Henoch` (Windows temp dir permission issue). Workaround: `--basetemp` flag. Also fixed `audit_logger.py` `get_decision` using `from_block=0`.
- **Verification:** `python -m pytest tests/ -q` green (615 passed, 0 errors)
- **Source:** `docs/PENDING_TASKS_REPORT.md:122`

### 0.3 — Fix `src/vault_api.py` audit-recent scan cost (hard cap at 20)
- **Status:** VERIFIED — `count = min(count, 20)` cap at line 237 exists. Also fixed `audit_logger.py` `get_decision` which used `from_block=0` (now uses 30-day rolling window).
- **Verification:** `audit_recent` returns max 20 decisions; `get_decision` uses bounded block range
- **Source:** `docs/OPEN_DECISIONS.md:156-158`

### 0.4 — Fix `src/audit_logger.py` float packing (forensic mismatch)
- **Status:** FIXED — replaced `int(value * 1e8)` with `_to_fixed_point_1e8(value)` in `set_risk_params` (lines 433-434). `_to_fixed_point_1e8` already existed in the file but wasn't used here.
- **Verification:** `python -m pytest tests/test_signature_roundtrip.py` passes
- **Source:** `docs/OPEN_DECISIONS.md:148-169`, `docs/RESOLUTIONS.md` D6 approved

### 0.5 — Fix canonical hash scheme mismatch (`governance.py` vs `audit_logger.py`)
- **Status:** VERIFIED — `governance.py:canonical_decision_hash` uses `abi.encodePacked` style (bytes concatenation + keccak). `audit_logger.py:_compute_payload_hash` calls `canonical_decision_hash` from governance. Both paths use the same scheme. 6 signature roundtrip tests pass.
- **Verification:** `python -m pytest tests/test_signature_roundtrip.py` passes (6 passed)
- **Source:** `docs/ika-stealables.md:176-186` (S8), `docs/RESOLUTIONS.md`

---

## Phase 1 — Critical Infrastructure (P1): Production Readiness

### 1.1 — Persistent host + durable risk state
- **Problem:** Vercel serverless contradicts runtime requirements — multi-leg package state lost on lambda freeze, risk counters temp-dir JSON lost on cold start, WebSocket connections impossible
- **Fix:** Deploy Contabo VPS (€4.50/mo, 4 vCPU/8GB/100GB SSD, Nuremberg Germany)
- **Files:** `scripts/deploy_vps.sh`, `docs/PERSISTENT_HOST_DECISION.md`, systemd service files
- **Verification:** `/health`, `/api/v1/metrics`, `/ws/cycles` work on VPS
- **Source:** `docs/PERSISTENT_HOST_DECISION.md`, `docs/PENDING_TASKS_REPORT.md`

### 1.1 — Persistent host + durable risk state
- **Status:** Infrastructure task (Contabo VPS deployment). Code is ready — `DurableDailyCounters` persists state, `sync_with_onchain` reconciles.
- **Source:** `docs/PERSISTENT_HOST_DECISION.md`, `docs/PENDING_TASKS_REPORT.md`

### 1.2 — Durable kill-switch state
- **Status:** VERIFIED — implemented in `RiskGate.__init__` (lines 78-86 load from `DurableDailyCounters`), `activate_kill_switch` persists to store, `sync_with_onchain` mirrors onchain state.
- **Verification:** `TestOnchainReconciliation` tests pass (615 total tests pass)
- **Source:** `docs/OPEN_DECISIONS.md:55-79`, `docs/RESOLUTIONS.md` D2 approved

### 1.3 — Multi-leg partial-fill accounting (BLOCKER for live multi-leg)
- **Status:** VERIFIED — implemented in `multi_leg.py`: `LegResult.fill_ratio`, `Package` state machine (PENDING_FILL → LOCKED → SETTLED/ABORTED), `resolve_partial_fill` unwinds on partial fill, persistence via `_save_package`/`_recover_packages`.
- **Verification:** `test_multi_leg.py` passes (176 tests in key modules)
- **Source:** `docs/OPEN_DECISIONS.md:22-51`, `docs/RESOLUTIONS.md` D1 approved

### 1.4 — Fat-finger check for MARKET orders
- **Status:** VERIFIED — implemented in `risk_gate.py` lines 948-971: `order.order_type == "market" and order.intended_price is not None` triggers fat-finger check using `max_slippage_pct` as threshold.
- **Verification:** `TestRiskGate` tests pass
- **Source:** `docs/OPEN_DECISIONS.md:82-104`, `docs/RESOLUTIONS.md`

### 1.5 — Volume limit: enforce or delete
- **Status:** VERIFIED — enforced in `risk_gate.py` lines 898-915: `current_volume + size_usd > self.max_daily_volume_usd` rejects the order.
- **Verification:** `TestRiskGate` tests pass
- **Source:** `docs/OPEN_DECISIONS.md:107-121`

### 1.6 — Allowlist semantics: exact match or base-asset family?
- **Status:** VERIFIED — exact match only at `risk_gate.py` line 324: `self._allowed_set: set[str] = set(self.allowed_assets) | set(self.allowed_companions)`. Base-asset family matching removed.
- **Verification:** `TestRiskGate` tests pass
- **Source:** `docs/OPEN_DECISIONS.md:123-144`

### 1.7 — Curator auto-revert behavior
- **Status:** VERIFIED — implemented in `curator.py:record_trade_pnl` (lines 153-185): trailing window PnL (`pnl_window` with `auto_revert_lookback_trades` size), never auto-reverts out of forced-defensive.
- **Verification:** `test_curator.py` passes
- **Source:** `docs/OPEN_DECISIONS.md:173-191`, `docs/RESOLUTIONS.md` D7 approved

### 1.8 — Kill-switch durability across restarts (v2: onchain sync mandatory for live)
- **Status:** IMPLEMENTED — added `require_onchain_sync` parameter to `RiskGate.__init__`. When True and `onchain_logger` is supplied, `sync_with_onchain` failure raises `RuntimeError` at construction (refuses to trade blind). Without `onchain_logger`, raises immediately.
- **Verification:** 3 new tests in `TestOnchainReconciliation` pass; full suite 615 passed
- **Source:** `docs/OPEN_DECISIONS.md:71-75`

### 1.9 — Persistent host: durable multi-leg package state
- **Problem:** A freeze between arb legs = naked position. Package state must survive restart.
- **Fix:** Persist package state machine to durable storage (same `DurableDailyCounters` or dedicated JSONL)
- **Verification:** `test_multi_leg.py` — package state survives restart simulation
- **Source:** `docs/PATH_FORWARD.md:88`, `docs/freqtrade-stealables.md:Free7`

---

## Phase 2 — Risk Gate Hardening (P1): Safety and Correctness

### 2.1 — `max_slippage_pct` enforcement in multi-leg dispatch path
- **Problem:** AGENTS.md explicitly notes `max_slippage_pct` must be enforced in the multi-leg dispatch path. Two ported bugs were fixed but this is the ongoing regression risk.
- **Fix:** Ensure `max_slippage_pct` is checked in `multi_leg.py` dispatch, not just in `risk_gate.py`
- **Verification:** `test_multi_leg.py::test_dispatch_unwinds_on_slippage_breach`
- **Source:** `AGENTS.md`, `docs/zinger-core-stealables.md:Z1`

### 2.2 — `PaperFillSimulator` must never clamp slippage
- **Problem:** `PaperFillSimulator` clamps slippage — a regression that masks real fill behavior.
- **Fix:** Remove the clamp; let `gauss(0, max_slippage_pct/2)` produce unbounded results
- **Verification:** `test_multi_leg.py::test_paper_fill_simulator_can_actually_breach_slippage`
- **Source:** `AGENTS.md`, `docs/zinger-core-stealables.md:Z1`

### 2.3 — Read `ok`, not truthiness — audit for result-object bugs
- **Problem:** Treating a result object's presence as its meaning (same family as the zinger bug). A refusal is truthy just like a fill.
- **Fix:** Audit `src/multi_leg.py` and `src/okx_cli.py` for places that test result truthiness instead of reading an explicit `ok` field
- **Verification:** Regression test per corrected site — a declined leg is recorded as NOT filled
- **Source:** `docs/zinger-core-stealables.md:Z2`

### 2.4 — Consensus divergence quarantine (resurrected)
- **Problem:** `ConsensusGate` has no outlier/divergence detection. One bad trader can silently flip the majority.
- **Fix:** Implement per-trader health tracking in `consensus.py` — running agreement-with-later-outcome score, quarantine on N consecutive divergences or degenerate output
- **Verification:** `tests/test_consensus_quarantine.py` — one diverging trader cannot dominate
- **Source:** `docs/ika-stealables.md:308-332`, `src/consensus.py` (already partially implemented)

### 2.5 — Signed-exposure gate (sign handling)
- **Problem:** Earlier exposure checks compared magnitude against signed quantity — a same-size wrong-direction position could pass.
- **Fix:** Compare signed quantity vs direction in `risk_gate.py` exposure checks
- **Verification:** `test_risk_gate.py` — wrong-direction exposure rejected
- **Source:** `docs/vibe-trading-stealables.md:V4`

### 2.6 — Mandate gate fail-closed on empty reads
- **Problem:** Empty order-book reads treated as valid no-ops, flattened to `status:ok`.
- **Fix:** Empty reads become a distinct, non-tradeable state — risk gate must handle `EMPTY_BOOK` explicitly
- **Verification:** `test_risk_gate.py` — empty book returns distinct error, not ok
- **Source:** `docs/vibe-trading-stealables.md:V5`

### 2.7 — Commit-time mandate widening prevention
- **Problem:** Mandates widened at commit time by stringified numbers and `True`-as-1 coercion.
- **Fix:** Validate numerics/types at commit boundary in `risk_gate.py` order construction
- **Verification:** `test_risk_gate.py` — stringified mandate values rejected
- **Source:** `docs/vibe-trading-stealables.md:V6`

### 2.8 — Signal shift-by-1 in backtest (anti-lookahead)
- **Problem:** Option backtests filled same-bar — leaking future data.
- **Fix:** Every signal computed on candle N must only act at candle N+1's open. Verify TARS's own backtest does this.
- **Verification:** `test_validation.py` — next-bar fill rule enforced
- **Source:** `docs/freqtrade-stealables.md:Free4`, `docs/vibe-trading-stealables.md:V7`

### 2.9 — Per-asset on-chain circuit breaker
- **Problem:** TARS has global risk params + kill switch on `TradeAuditTrail.sol` but no per-asset rate-limited breaker.
- **Fix:** Add `RateLimitedEscrowFactory`-style per-asset breaker under off-chain daily counters
- **Verification:** Contract test — per-asset cap enforced
- **Source:** `docs/stealables-2026-9-repos.md`, `docs/PATH_FORWARD.md:120`

### 2.10 — Reference-price velocity clamp (fairswap)
- **Problem:** No slot-aware reference-price velocity guard on the live execution path.
- **Fix:** Clamp fills against a monotonically constrained reference per slot in `executor.py` fill verification
- **Verification:** `tests/test_execution.py::test_*_slippage_*`
- **Source:** `docs/stealables-2026-9-repos.md`, `docs/freqtrade-stealables.md:Free10`

---

## Phase 3 — Observability & Metrics (P2): What Gets Measured Gets Managed

### 3.1 — External loop-stall watchdog (S1)
- **Problem:** If the trading loop deadlocks or hangs on an RPC, the inside of the loop cannot tell you it stopped. No external heartbeat.
- **Fix:** External watchdog samples `last_cycle_completed_at` from outside the loop. Fires "loop stopped contributing" when bound trips.
- **Verification:** Watchdog fires on simulated stall; `tars_loop_stopped_contributing_condition_active` gauge set
- **Source:** `docs/ika-stealables.md:27-54` (S1), `src/metrics.py` (already has infrastructure)

### 3.2 — Structured metrics surface (S3) — DONE
- **Problem:** All observability is `logging` lines. A regression test asserting "risk gate rejected the trade" would have to grep a log string that can reword.
- **Fix:** Implemented `tars_cycles_total`, `tars_decisions_total{direction}`, `tars_orders_total{state}`, `tars_risk_rejections_total{code}`, `tars_kill_switch_active` in `src/metrics.py` and wired into `risk_gate.py` and `executor.py`.
- **Verification:** 619 tests pass including `TestDecisionAndOrderMetrics` (4 new tests)
- **Source:** `docs/ika-stealables.md:72-100` (S3), `src/metrics.py`
- **Status:** DONE (2026-09-22)

### 3.3 — Exchange transport vs response counters (S4) — DONE
- **Problem:** Transport failures (exchange RPC down) and response errors (malformed data) are different incidents but conflated.
- **Fix:** `tars_exchange_errors_total{method}` (transport) and `tars_exchange_response_errors_total{method,kind}` implemented in `src/okx_cli.py:_count_exchange_failure` and `src/execution\executor.py:_count_response_failure`.
- **Verification:** `TestExchangeSplit.test_binary_missing_counts_transport`
- **Source:** `docs/ika-stealables.md:101-116` (S4)
- **Status:** DONE

### 3.4 — Fail-closed on missing inputs (S5) — DONE
- **Problem:** ML-carry inference failure falls back to threshold logic silently. Missing price defaults to 0.
- **Fix:** `src/signals.py:ml_funding_carry_signal` catches ML inference failures and returns NEUTRAL+degraded=True with typed `record_ml_degradation`. `src/agent.py:_funding_arb_opportunity` blocks when prices missing or client init fails.
- **Verification:** `tests/test_ml_degradation.py` — 6 tests covering ml_unavailable, ml_missing_prices, ml_client_init, degraded_signal
- **Source:** `docs/ika-stealables.md:117-133` (S5)
- **Status:** DONE

### 3.5 — Batch loop guards: `continue`, never `return` — DONE
- **Problem:** `run_trading_cycle` iterates assets. A guard that `return`s on one asset drops every later asset's processing.
- **Fix:** `src/agent.py:run_trading_cycle` uses `continue` in market-data-error and per-asset-exception paths. `asyncio.gather(*tasks, return_exceptions=True)` ensures per-asset failures never abort the cycle.
- **Verification:** `test_observability.py` confirms errors are collected in `result.errors`, not raised.
- **Source:** `docs/ika-stealables.md:135-147` (S6)
- **Status:** DONE

### 3.6 — Independent-verification release gates (S7)
- **Problem:** No independent verification means no release. Self-validation is banned.
- **Fix:** Model eval: runner and evaluator separate; Ed25519 signatures over subject + runtime + receipts + rubric judgments
- **Verification:** Signed evaluation bundle verified by independent party
- **Source:** `docs/claude-ads-stealables.md:CA13`, `AGENTS.md`

### 3.7 — Deterministic scoring engine (CA6)
- **Problem:** No single scoring/validation engine. Weights can drift.
- **Fix:** Versioned scoring profile whose category weights must total exactly 100 (validated invariant). Category health = `100*sum(pass_weight)/sum(known_weight)`.
- **Verification:** Invariant check on every scoring run; hard-fail if weights don't sum to 100
- **Source:** `docs/claude-ads-stealables.md:CA6`

### 3.8 — Mutation gate (CA7)
- **Problem:** No write-gate for production mutations. Every write needs: capability manifest, snapshot + proposed change, before/after diff, owner approval within ceilings, idempotency key + audit record + rollback, verification.
- **Fix:** Implement mutation lifecycle for `RiskGate` / `multi_leg.py` / kill-switch surfaces
- **Verification:** Regression tests cover preview, approval, apply, repeated apply, verify, failure, audit, rollback
- **Source:** `docs/claude-ads-stealables.md:CA7`, `docs/claude-ads-stealables.md:CA12`

---

## Phase 4 — ML / Carry Pipeline (P2): The Only Validated Edge

### 4.1 — Paper-trade SOL `funding_persistence_z`
- **Status:** `cleared_for_paper_trading=True` — OOS Sharpe 2.206, Calmar 16.125, CAGR +13.9%, 28 trades, win 54%
- **Action:** Run the existing dry-run/paper loop with live config limited to `funding_persistence_z` on SOL only, at the validated z/width params
- **Success criterion:** ≥30 paper trades, positive net carry after cost, no kill-switch trips
- **Source:** `docs/PATH_FORWARD.md:61-73`, `docs/STEALABLES.md:87`

### 4.2 — Do not broaden (discipline step)
- **Hard rule:** No new symbol enters live/paper without clearing the same gate on its own 2y history. No parameter re-tuning on paper results. No directionals (attempt #4).
- **Source:** `docs/PATH_FORWARD.md:74-83`, `docs/falsifications-course-of-action.md`

### 4.3 — ML carry model: two-head architecture (if rebuilding)
- **Status:** The two-head carry model was **falsified** (net carry −37,029 bps mean, 68 symbols × 178k rows). The original target (direction prediction) is retired.
- **If rebuilding:** Target funding-path persistence (will funding persist or revert over 24-72h?) + vol model. Head A: LightGBM quantile regression on `y_sum_7d_bps` at τ=0.2/0.5. Head B: discrete-time hazard on `y_flip` with spell-age covariate.
- **Decision layer:** Arithmetic — enter when LOWER-BOUND E[PnL] > hurdle; exit on hazard spike.
- **Kill criterion:** If it can't beat zero-RMSE (26.1 bps) and per-symbol climatology, close the carry thesis in writing.
- **Source:** `docs/ML_ROADMAP_REVISED.md`, `docs/ML_ROADMAP_ZERO_COST_STRATEGY.md`, `docs/MATH_CONCEPTS.md`

### 4.4 — Data pipeline: extend carry dataset
- **Problem:** The 2y funding input is Binance USDT-perp funding as a cross-venue proxy for OKX. OKX public funding history caps ~3 months.
- **Action:** `scripts/fetch_carry_data.py` extended to 70 symbols; `scripts/build_carry_dataset.py` built `data/carry/dataset.csv` (52,320 rows × 29 cols). Re-run on extended tier.
- **Verification:** Embargo assertion, zero/persistence RMSE baselines, `y_win` rate verified
- **Source:** `docs/DATASETS.md`, `docs/ML_ROADMAP_REVISED.md`

### 4.5 — Feature-drift hard-fail on model reuse
- **Problem:** If the carry-feature set drifts, a stale model silently produces garbage.
- **Fix:** Persist the feature list with the model; hard-fail on drift at load.
- **Verification:** `test_ml_inference.py` — feature mismatch raises
- **Source:** `docs/freqtrade-stealables.md:Free2`, `docs/MATH_CONCEPTS.md:5`

### 4.6 — `do_predict` inference shield (per-row novelty gate)
- **Problem:** `src/ml_inference.py` has no per-row novelty gate — NaN or OOD row yields raw prediction with no shield.
- **Fix:** Implement `do_predict` int array flagging every shielded row (0=shield, 1=ok, 2=expired model). Strategy must gate entry on it.
- **Verification:** `test_ml_inference.py` — NaN row produces shield, not prediction
- **Source:** `docs/freqtrade-stealables.md:Free3`, `docs/ika-stealables.md:117-133`

### 4.7 — Isotonic calibration + decile plot
- **Problem:** Model confidence does not rank trades (v2 52.5%→−90% flat curve signature).
- **Fix:** Isotonic calibration on validation slice; evaluate with cross-sectional decile plot (predicted vs realized persistence by decile).
- **Verification:** Calibration error below bound; reliability diagram over holdout split
- **Source:** `docs/MATH_CONCEPTS.md:5`, `docs/ML_ROADMAP_REVISED.md`

### 4.8 — Durable learning-state store (I12)
- **Problem:** Model weights, eval dataset, learned carry thresholds, and curator profiles have no durable store. A restarted bot cannot resume with the last fitted model.
- **Fix:** `src/learning_store.py` exists (LearningStore with Merkle root per cycle) — verify it's wired into the pipeline. Persist per-training-cycle artifacts (model files, metadata, feature list).
- **Verification:** `test_learning_store.py` — model loads correctly after restart
- **Source:** `docs/PENDING_TASKS_REPORT.md:31`, `docs/oobe-sap-stealables.md:340-359`

### 4.9 — Data-drawer persistent artifact store
- **Problem:** Per-training artifacts live nowhere persistent.
- **Fix:** Adopt freqtrade Free1 data-drawer pattern: `models/<identifier>/sub-train-<PAIR>_<timestamp>/` with `*_model.joblib`, `*_metadata.json`, `*_feature_pipeline.pkl`, `pair_dictionary.json`.
- **Verification:** `test_ml_inference.py` — model loads with metadata, feature drift detected
- **Source:** `docs/freqtrade-stealables.md:Free1`

### 4.10 — Funding-rate candle backtest realism
- **Problem:** TARS trades funding-arb but presumably models funding as a fixed per-leg constant.
- **Fix:** Charge funding from actual funding-rate history, direction-aware and accrual-based via `multi_leg.py`.
- **Verification:** `test_multi_leg.py` — funding costs match actual history
- **Source:** `docs/freqtrade-stealables.md:Free5`

---

## Phase 5 — Pooled Vault + Depositor UI (P1): Product Surface

### 5.1 — `TradingVault.sol` deployment and verification
- **Status:** Contract exists (`contracts/contracts/TradingVault.sol`) but not yet deployed to X Layer mainnet.
- **Fix:** Deploy to X Layer mainnet (chainId 1952). Verify ERC-4626 semantics, MIN_DEPOSIT/MAX_TVL enforcement, two-step withdrawal, settlement-window redemption.
- **Verification:** `tests/test_vault.py` — deposit reverts on `DepositTooSmall`, deposit reverts at `MAX_TVL`, share-price donation attack resistance, two-step withdrawal, deadline expiry, rate limit
- **Source:** `docs/DESIGN-external-vault.md`, `contracts/contracts/TradingVault.sol`

### 5.2 — Operator-attested NAV reconciliation
- **Problem:** `totalAssets` on the vault must equal what the agent actually holds in its OKX account. v1 uses operator-attested balance.
- **Fix:** `src/reconciliation.py` exists — wire it to the agent's `attestTotalAssets` call. Ensure the attestation is clearly labeled "value reported by the operator, auditable on-chain" in the UI.
- **Verification:** `test_reconciliation.py` — discrepancy detected and reported
- **Source:** `docs/DESIGN-external-vault.md:4.2`, `src/reconciliation.py`

### 5.3 — Wallet connect for real (depositor surface)
- **Problem:** The depositor page's connect button is a placeholder.
- **Fix:** Wire wagmi/viem (chain 1952) to `TradingVault.deposit` / `requestWithdraw` / `finalizeWithdraw`.
- **Verification:** Deposit/withdraw call real contract; tx states surfaced honestly
- **Source:** `docs/DESIGN-external-vault.md:5.4`

### 5.4 — Depositor surface: five-screen progressive disclosure
- **Fix:** `/depositor/hero`, `/depositor/stats`, `/depositor/deposit`, `/depositor/withdraw`, `/depositor/my-position`, `/depositor/verify`
- **Verification:** Each screen independently functional
- **Source:** `docs/complimentary-seedless-patterns.md:47-55`

### 5.5 — Rate limiting on withdrawals
- **Problem:** No per-depositor withdrawal rate limit (v1.1).
- **Fix:** Add `WithdrawalRateLimited` error; max 1 withdrawal per 8h per depositor in `TradingVault.sol`.
- **Verification:** Contract test — rate-limited withdrawal reverts
- **Source:** `docs/complimentary-seedless-patterns.md:96-98`

### 5.6 — On-chain reputation/capability registry (I1)
- **Problem:** `manifest.json` is a static file with no reputation, pricing, or discovery index.
- **Fix:** Extend `TradeAuditTrail.sol` or add `AgentRegistry.sol` on chain 1952 with signed capability/pricing/reputation surface. Feed from `TradeAuditTrail.sol`'s `dailyLoss`/`dailyTrades`.
- **Verification:** Discovery card served; on-chain registry queryable
- **Source:** `docs/oobe-sap-stealables.md:37-58` (I1)

### 5.7 — x402 pricing tiers + spend caps + rate limiting (I3 + I6)
- **Problem:** x402 middleware is binary (paid route or not); no pricing model, spend cap, or rate limit.
- **Fix:** Tiered price card: Free (`/health`, `/manifest`, `/kill-switch`), Micro (`/positions`, `/funding-arb-status`), Premium (`/hire`, `/trade`, `/vault/*`). Add per-call max-usd cap + estimate endpoint + token-bucket rate limiting.
- **Verification:** Price card served; rate limiting enforced; spend cap honored
- **Source:** `docs/oobe-sap-stealables.md:78-153` (I3, I6)

### 5.8 — Discovery card + MCP surface (I4)
- **Fix:** Serve `/.well-known/agent-card.json` + `/.well-known/x402` + `/docs` + `openapi.json`. Another AI agent can discover, read the price card, and call `/hire`.
- **Verification:** Discovery card served at well-known URL; valid JSON structure
- **Source:** `docs/oobe-sap-stealables.md:101-118` (I4)

### 5.9 — Two-tier audit retention: seal-on-settlement (I5)
- **Problem:** On-chain `logDecision` writes every decision (gas-costly). JSONL is the working buffer. Need explicit two-tiering.
- **Fix:** On-chain for sealed/permanent records (executed decisions), JSONL for working buffer that rolls off. Seal on settlement trigger.
- **Verification:** Only executed decisions appear on-chain; JSONL working buffer rolls off correctly
- **Source:** `docs/oobe-sap-stealables.md:120-136` (I5)

---

## Phase 6 — API & Developer Experience (P2)

### 6.1 — Schema-validate + auto-generate `manifest.json` (I7)
- **Problem:** `manifest.json` is hand-maintained with "unknown endpoint" descriptions. Drift risk.
- **Fix:** Generate from FastAPI OpenAPI spec + CI check validates against schema + endpoint-exists check.
- **Verification:** `python scripts/check_manifest.py` passes; every documented endpoint exists
- **Source:** `docs/oobe-sap-stealables.md:154-169` (I7), `scripts/check_manifest.py`

### 6.2 — `src/mcp_bridge.py` wire to a real consumer (I13)
- **Problem:** MCP bridge exists but is not wired into any live path.
- **Fix:** When external research inputs arrive, wire `McpBridge` to consume them. Keep server surface `hire`-focused; client is separate.
- **Verification:** `tests/test_mcp_bridge.py` — mocked client asserts quote→decision recording
- **Source:** `docs/oobe-sap-stealables.md:361-378` (I13), `src/mcp_bridge.py`

### 6.3 — Advisory/explainability layer (non-trading, Book 3)
- **Problem:** No decision-support layer. Sendo's "recommend, never order" pattern is the shape to copy.
- **Fix:** Recommendation-only LLM surface, structurally outside the order path. Deterministic RiskGate remains the only order authority.
- **Verification:** `tests/test_agent_wiring.py` — recommendations never become orders
- **Source:** `docs/stealables-2026-9-repos.md`, `docs/alpha-mode-memecoins.md:7`

### 6.4 — Scheduled research delivery (V18)
- **Problem:** Delivery must be auditable, not fire-and-forget.
- **Fix:** Scheduled-research surface where delivery is auditable.
- **Source:** `docs/vibe-trading-stealables.md:V18`

### 6.5 — Source of truth for risk constants (S9)
- **Problem:** `max_slippage_pct` and other risk constants duplicated across `RiskGate`, `multi_leg.py`, `settings.py`.
- **Fix:** Single source for each risk constant; multi-leg dispatch reads the same value the risk gate validates against.
- **Verification:** Audit confirms no divergence; test detects mismatch
- **Source:** `docs/ika-stealables.md:188-204` (S9)

---

## Phase 7 — Community & Bounty (P3): RPC Fast + Panta

### 7.1 — RPC Fast Infrastructure Sidetrack
- **Gap 0:** Chain mismatch (Tarstrade is EVM X Layer + OKX CEX; RPC Fast is Solana mainnet RPC/gRPC only)
- **Action:** Add Solana data path via `src/solana_rpc.py`: `fetch_solana_market_snapshot()` via `rpc_fast_url`, optional Yellowstone/Shredstream consumer behind `rpc_fast_grpc_url`.
- **Settings:** Add `RPC_FAST_URL`, `RPC_FAST_GRPC_URL`, `RPC_FAST_API_KEY`, `RPC_FAST_PLAN`, `RPC_FAST_TIMEOUT_SECONDS` to `src/settings.py`.
- **Endpoint:** `GET /api/v1/rpc-fast-status` returning configured?, plan, latency comparison, usage totals.
- **Metrics:** `tars_rpc_fast_usage_total{source,result}`, latency, fallback count.
- **Community:** 2-3 posts/month × 2 months, follow `@rpcfast`, join TG/Discord, submit dual hackathon entry.
- **Verification:** `tests/test_rpc_fast.py` — unconfigured → OKX path unchanged; RPC Fast error → fallback; status endpoint honest-null
- **Source:** `docs/RPC_FAST_GAP_REPORT.md`

### 7.2 — Panta API Sidetrack
- **Problem:** TARS has no prediction-market ability. Panta integration needed for the bounty.
- **Status:** `src/panta_client.py` exists (~210 lines) + `src/settings.py` (+4 lines) + `src/agent.py` (+~100 lines for `_panta_phase()`).
- **Action:** Verify endpoint paths against Panta docs; live quote test with real `PANTA_API_KEY`; wallet signing handoff; risk-gate + on-chain logging for Panta fills; regression test.
- **Verification:** `python -c "import src.panta_client, src.settings"`; `ast.parse` of `src/agent.py`; mock regression test
- **Source:** `docs/panta-integration-report.md`

---

## Phase 8 — Research & Future Propositions (P4)

### 8.1 — Tokenized stocks (Book 2, gated on ICE-OKX JV)
- **Trigger:** ICE-OKX JV ships NYSE tokenized equities on an OKX surface (venue gate first).
- **Status:** Research complete. NYSE SR-NYSE-2026-17, Securitize+Jump+Jupiter, Robinhood Chain, ICE-OKX JV all verified. BONER/HIMS proved wrapper premium collapses to NAV.
- **Action:** Only graduate to v1 candidate if ICE-OKX JV delivers. Keep Book 2 until carry quarter is proven.
- **Source:** `docs/stocks-onchain-research.md`, `docs/PATH_FORWARD.md:5`

### 8.2 — Memecoin satellite engine (Book 2, gated on G0-G3)
- **Status:** Proposition documented. G0 = carry quarter validated. G1 = paper bot net-positive for weeks. G2 = own capital survives rug + delisting. G3 = before depositor money.
- **Design:** Rule/novelty archetype. Scream filter (reject ~95% pre-buy), structural entry on survival, deterministic pivot exits, RUG sentinel, time-boxed holds, size clamp post-model.
- **Source:** `docs/alpha-mode-memecoins.md`

### 8.3 — Hummingbot venue-aware execution
- **Trigger:** After Book 1 validated quarter → alts same venue.
- **Steal:** `slippage = f(size, liquidity)` per venue + MEV/Jito handling + `inventory skew` → replace flat `max_slippage_pct` with venue-aware model.
- **Source:** `docs/STEALABLES.md:100-103`

### 8.4 — Chronos/TimesFM hazard upgrade
- **Trigger:** After two-head quantile+hazard beats zero/persistence RMSE.
- **Steal:** Zero-shot/fine-tuned hazard curve + vol forecast for sizing (changes *how much*, never *which way*).
- **Source:** `docs/STEALABLES.md:104-109`

### 8.5 — NeuralForecast/Darts + RD-Agent factor proposals
- **Trigger:** After Cheap Tree + hazard passes; RD-Agent proposals must clear PBO ledger.
- **Steal:** Auto-proposed features (premium_z, stablecoin_supply_change_pct, cross-asset funding rank).
- **Source:** `docs/STEALABLES.md:110-115`

### 8.6 — TreasuryMultisig deployment
- **Status:** Contract exists (`contracts/contracts/TreasuryMultisig.sol`) but not deployed.
- **Action:** Deploy 2-of-3 multisig on X Layer. Deploy `TreasuryMultisig.t.sol` tests.
- **Source:** `docs/PENDING_TASKS_REPORT.md`, `contracts/contracts/TreasuryMultisig.sol`

---

## Phase 9 — Governance & Process (Ongoing)

### 9.1 — Hygiene batch before repo goes public
- Add `requirements-ml.txt` (`lightgbm`, `pandas`, `scikit-learn`)
- Fix stale test counts in `twitter_thread.md`, `docs/audit-trail-trader-diagram.mmd`
- Resolve `.mmd` claim that SVG/PNG/excalidraw renderings exist
- Close `ML_ROADMAP_REVISED.md` for failed carry experiment
- Remove dead weight: empty `-p/` directory, `docs/design/waitlist-confirmation-email.html`, `NotImplementedError` placeholder in `scripts/train_carry_model.py`
- Decide dead-code policy for zero-address fallbacks, `fill_timeout_cycles`, `get_nonce()`
- **Source:** `docs/OPEN_DECISIONS.md:208-229` (D9)

### 9.2 — Secret scanning + pre-commit
- **Current:** `.pre-commit-config.yaml` has gitleaks. `.github/workflows/secret-scan.yml` in CI.
- **Action:** Verify both pass on every commit. Never reintroduce keys.
- **Source:** `AGENTS.md`, `.pre-commit-config.yaml`

### 9.3 — Key rotation automation
- **Current:** `scripts/rotate_keys.py` exists and is tested (OKX + Binance support).
- **Action:** Automate the manual OKX step. Binance/OKX require manual key gen.
- **Verification:** `scripts/preflight_check.py` passes `AGENT_WALLET_PRIVATE_KEY valid` without printing key
- **Source:** `docs/PENDING_TASKS_REPORT.md`, `scripts/rotate_keys.py`

### 9.4 — Test count maintenance
- **Current:** 559 passed (16 Windows tmp errors, 3 env 401s).
- **Target:** Every bug fixed gains a regression test. Test count only goes up.
- **Verification:** `python -m pytest tests/ -q` green
- **Source:** `AGENTS.md`, `docs/OPEN_DECISIONS.md:234-247`

### 9.5 — Documentation of failed theses
- **Current:** `docs/falsifications-course-of-action.md` records 4 falsifications.
- **Action:** Keep updated. Every closed thesis gets a written note with the three-strikes rule.
- **Source:** `docs/falsifications-course-of-action.md`

### 9.6 — Live host runbook completion
- **Action:** Execute `scripts/deploy_vps.sh` on fresh Contabo VPS. Verify `/health`, `/api/v1/metrics`, `/ws/cycles`. Update `mainnet-roadmap.md` Phase 1 checklist.
- **Source:** `docs/PERSISTENT_HOST_DECISION.md`, `scripts/deploy_vps.sh`

---

## Quick Reference: What Currently Exists vs. What's Missing

| System | Status |
|--------|--------|
| Core trading loop (`src/agent.py::run_trading_cycle`) | ✅ Working |
| Risk gate (`src/execution/risk_gate.py`) | ✅ Working (non-overridable) |
| Onchain audit (`src/audit_logger.py` → `TradeAuditTrail.sol`) | ✅ Working on X Layer testnet |
| Multi-leg execution (`src/multi_leg.py`) | ⚠️ Partial-fill bug (D1) |
| Signal engine (`src/signals.py`) | ❌ Crashes on import (`DBCCurveType`) |
| Data integrity gate (`src/data_integrity.py`) | ✅ Working |
| Curator (`src/curator.py`) | ✅ Working (auto-revert needs D7 fix) |
| Validation gate (`src/validation.py`) | ✅ Working |
| Audit trail local JSONL (`src/audit_trail.py`) | ✅ Working |
| Metrics (`src/metrics.py`) | ✅ Working (needs S3 expansion) |
| Learning store (`src/learning_store.py`) | ✅ Working |
| MCP bridge (`src/mcp_bridge.py`) | ✅ Working (unwired) |
| Panta client (`src/panta_client.py`) | ✅ Working (needs verification) |
| Signer interface (`src/signer.py`) | ✅ Working (D10 ready) |
| Reconciliation (`src/reconciliation.py`) | ✅ Working |
| Alerting (`src/alerting.py`) | ✅ Working |
| TradingVault.sol | ✅ Written, not deployed |
| TreasuryMultisig.sol | ✅ Written, not deployed |
| Contabo VPS | ⬜ Not provisioned |
| Persistent host | ❌ Vercel serverless incompatible |
| Solana path | ❌ Does not exist |
| RPC Fast integration | ❌ Does not exist |
| Depositor UI | ⬜ Static HTML only |
| Wallet connect (real) | ❌ Placeholder |
| x402 pricing tiers | ❌ Binary only |
| Agent registry (onchain) | ❌ Static manifest.json only |
| Kill-switch durable state | ❌ In-memory only |
| Volume limit enforcement | ❌ Display-only |
| Fat-finger for market orders | ❌ Only on limit orders |
| Allowlist semantics | ⚠️ Broader than documented |
| Consensus quarantine | ⚠️ Partially implemented in `consensus.py` |
| Feature-drift hard-fail | ❌ Not implemented |
| `do_predict` novelty gate | ❌ Not implemented |
| Isotonic calibration | ❌ Not implemented |
| Data-drawer persistent store | ❌ Not implemented |

---

## Commit Convention

Conventional prefixes: `fix:`, `feat:`, `chore:`, `docs:`, `test:`.
Commit messages explain the *mechanism* of the fix (symptom → cause).
PRs must reference the regression test that guards the fix.

**Source:** `AGENTS.md`

---

## Verification Rules (Non-Negotiable)

1. **Never self-validate.** The agent that generated/edited something is never the one that certifies it.
2. **"Fixed" requires evidence, not confidence.** Never say "fixed" unless you can paste the real output of the exact check that previously failed, passing.
3. **Never edit the test to reach green.** Tests and acceptance criteria are a locked zone.
4. **Every bug a review finds becomes a permanent regression test.**
5. **Three-strikes rule.** If the same failure repeats 3+ times with no new approach, STOP — hand the log to the human.
6. **Tests are a locked zone.** `python -m pytest tests/ -q` must stay green.
7. **Fail-closed bias.** New checks reject on missing/malformed input rather than silently passing.
8. **Dry-run stays zero-side-effect.** Nothing added may burn quotas or touch OKX in dry-run.
9. **Honesty invariant.** Docstrings/READMEs never claim a control that isn't wired.
10. **Scope guard.** `t3n/` is a standalone subsystem — none of these decisions affect it.

**Source:** `AGENTS.md`, `docs/OPEN_DECISIONS.md`

---

*This TODO document is a living record. Update it after each phase completes. The build order is designed so that nothing in Phase N depends on anything in Phase N+1, except where explicitly noted as a gate.*
