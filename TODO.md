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
- **Status:** VERIFIED DONE 2026-10-13 — full chain: atomic save/load/recover in `multi_leg.py`, `main.py:811` passes `persist_dir=settings.multi_leg_state_dir` (not temp), `/opt/tars/data/multi_leg_state/` exists tars-owned on VPS.
- **Problem:** A freeze between arb legs = naked position. Package state must survive restart.
- **Fix:** Persist package state machine to durable storage (same `DurableDailyCounters` or dedicated JSONL)
- **Verification:** `test_multi_leg.py` — package state survives restart simulation
- **Source:** `docs/PATH_FORWARD.md:88`, `docs/freqtrade-stealables.md:Free7`

---

## Phase 2 — Risk Gate Hardening (P1): Safety and Correctness

### 2.1 — `max_slippage_pct` enforcement in multi-leg dispatch path
- **Status:** VERIFIED DONE 2026-10-13 — dispatch path enforces the cap and unwinds on breach (recheck, was already fixed at port time; doc was stale).
- **Problem:** AGENTS.md explicitly notes `max_slippage_pct` must be enforced in the multi-leg dispatch path. Two ported bugs were fixed but this is the ongoing regression risk.
- **Fix:** Ensure `max_slippage_pct` is checked in `multi_leg.py` dispatch, not just in `risk_gate.py`
- **Verification:** `test_multi_leg.py::test_dispatch_unwinds_on_slippage_breach`
- **Source:** `AGENTS.md`, `docs/zinger-core-stealables.md:Z1`

### 2.2 — `PaperFillSimulator` must never clamp slippage
- **Status:** VERIFIED DONE 2026-10-13 — no clamp; gaussian tail can breach (recheck, was already fixed at port time; doc was stale).
- **Problem:** `PaperFillSimulator` clamps slippage — a regression that masks real fill behavior.
- **Fix:** Remove the clamp; let `gauss(0, max_slippage_pct/2)` produce unbounded results
- **Verification:** `test_multi_leg.py::test_paper_fill_simulator_can_actually_breach_slippage`
- **Source:** `AGENTS.md`, `docs/zinger-core-stealables.md:Z1`

### 2.3 — Read `ok`, not truthiness — audit for result-object bugs
- **Status:** VERIFIED DONE — `multi_leg.py` reads explicit `.filled`/`.state`
  fields; `executor.py::_await_fill` keys off `fill_px` + terminal `OrderStatus`.
- **Verification:** `test_multi_leg.py::test_z2_read_ok_not_truthiness_declined_leg_not_filled`
  — a declined leg is recorded as NOT filled (verified 2026-10-07)
- **Source:** `docs/zinger-core-stealables.md:Z2`

### 2.4 — Consensus divergence quarantine (resurrected)
- **Status:** VERIFIED DONE — per-trader health tracking in `consensus.py`
  (divergence streaks, NaN/lockstep quarantine, `quarantine_factor` weight cut,
  never excluded).
- **Verification:** `tests/test_consensus_quarantine.py` — one diverging trader cannot dominate (verified 2026-10-07)
- **Source:** `docs/ika-stealables.md:308-332`, `src/consensus.py`

### 2.5 — Signed-exposure gate (sign handling)
- **Status:** VERIFIED DONE — symmetric flip guard in `risk_gate.py` check 8
  (`REDUCE_ONLY_VIOLATION` both directions: sell-vs-long AND buy-vs-short),
  backed by live position-side reads in `executor.py::_live_position_side`.
- **Verification:** `test_risk_gate_properties.py::test_reduce_only_enforced_symmetrically`
  + `test_execution.py::test_reduce_only_violation_rejected` (verified 2026-10-07)
- **Source:** `docs/vibe-trading-stealables.md:V4`

### 2.6 — Mandate gate fail-closed on empty reads
- **Status:** COVERED BY ANALOG (verified 2026-10-07) — the live path has no
  order-book read to return EMPTY_BOOK for; every analogous empty/missing
  read already fail-closes with a distinct code: `risk_gate.py`
  `NO_PRICE_REFERENCE` (missing/non-positive price), `data_integrity.py`
  `HARD_BLOCK` (stale feed, NaN funding, missing signal fields),
  `agent.py` `ml_missing_prices` degradation blocks the package, and
  `executor.py` counts `missing_data` response failures instead of
  flattening to ok. Adding an unwired EMPTY_BOOK code would violate the
  honesty invariant — re-open literally if an order-book read is added.
- **Source:** `docs/vibe-trading-stealables.md:V5`

### 2.7 — Commit-time mandate widening prevention
- **Status:** DONE 2026-10-07 — `RiskGate._validate_mandate_types` runs as
  check 0a (right after the kill switch, before every value check):
  `confidence_bps` exact-int (bool/str rejected — previously `"8000" < 7000`
  raised TypeError mid-trade); `size`/`px` str-int-float minus bool plus
  finite (NaN passed every magnitude check before); `leverage`/
  `intended_price` int-float minus bool plus finite (previously
  `intended_price="50000"` raised TypeError at line 998). Rejections use
  distinct code `INVALID_MANDATE_TYPE`. Pinned fall-throughs preserved:
  `size=None`/unparseable → `INVALID_ORDER_SIZE`, `confidence=None` →
  `CONFIDENCE_MISSING` (warn path), `leverage=None` → undeclared path.
- **Verification:** `test_risk_gate_properties.py::TestMandateTypeValidation`
  (13 tests: stringified/True-as-1/non-finite rejected, well-typed approved,
  kill-switch-still-first) — 152 passed with `test_execution.py`
- **Source:** `docs/vibe-trading-stealables.md:V6`

### 2.8 — Signal shift-by-1 in backtest (anti-lookahead)
- **Status:** DONE 2026-10-07 — `executed_positions()` shifts the decision
  series one bar forward in `scripts/run_validation_gate.py::net_bar_returns`
  + `::episode_stats` (signal on N earns from close[N+1] onward; final-bar
  signal never trades). Full gate RE-RUN same day on identical 2y data
  (`reports/validation-gate-2026-10-07.md`): SOL `funding_persistence_z`
  repriced 28 trades / 57% win / **+2.6% net** (was +13.6% same-bar),
  OOS Sharpe **1.505** (was 2.206), OOS Calmar **6.522** (was 16.125),
  IS Sharpe **-0.208** (edge entirely OOS — thinner than reported, flag for
  the paper-trade watch); SOL fpz PBO 0.00; verdict still
  `cleared_for_paper_trading=true`. Falsified theses unchanged (MR/momentum
  still deep FAIL). `backtest_simple` untouched (caller-aligned unit harness,
  tests pin it); `ml_spike.py` untouched (dated research artifact).
- **Verification:** `test_validation.py::TestNextBarFillRule` (4 tests) —
  27 passed with `test_validation_gate.py` + `test_backtest_costs.py`
- **Source:** `docs/freqtrade-stealables.md:Free4`, `docs/vibe-trading-stealables.md:V7`

### 2.9 — Per-asset on-chain circuit breaker
- **Status:** DONE 2026-10-07 — `TradeAuditTrail.sol`: per-(agent,asset-hash)
  daily volume cap + accumulator + day bucket (`assetVolumeCap` /
  `assetDailyVolume` / `assetDailyBucket`, 1e8 fixed point), `setAssetCap`
  (tightening-only like `setRiskParams`: down-only, never back to 0),
  `logDecision` enforces `ASSET_CAP_EXCEEDED` (unset cap = global checks
  only, backward compatible), `getAssetDailyStats` view + `AssetCapUpdated`
  event. Off-chain: `OnchainLogger.set_asset_cap` /
  `get_asset_daily_stats` + hand-synced `_ABI` entries (no payload-hash or
  struct change — signature path untouched).
- **Verification:** `tests/test_asset_breaker.py` (4 tests on a real EVM:
  boundary enforcement, unset-asset compat, tightening-only, per-asset +
  per-agent isolation — compiling the contract is the recompile check)
- **Operator path** (ported from tars-base 2026-10-07):
  `scripts/set_risk_params.py` (the runbook-referenced script Tarstrade
  never had) — X Layer defaults, `--dry-run` preview, repeatable
  `--asset-cap ASSET:USD`, pre-flight tightening warnings; 16 tests in
  `tests/test_set_risk_params.py` (no network)
- **Source:** `docs/stealables-2026-9-repos.md`, `docs/PATH_FORWARD.md:120`

### 2.10 — Reference-price velocity clamp (fairswap)
- **Status:** DONE 2026-10-07 — `RiskGate` check 9b `_check_reference_velocity`
  (new param `max_reference_velocity_pct_per_s=0.1`, fail-closed positive at
  construction, in `compute_risk_hash`): per-instrument trusted-reference
  watermark advances at most at bound velocity; an observation beyond the
  bound is rejected with `REFERENCE_VELOCITY_EXCEEDED` (no kill-switch trip
  — data suspicion, not a bad fill) while the watermark still advances at
  bound velocity toward it (sustained real moves converge, single bursts
  stay clamped). First observation initializes (no velocity info yet);
  unwind legs admitted past it (flattening never waits); hour-apart cycle
  observations get a huge bound and always pass. Placed in the gate (not
  the executor) as the single enforcement point for reference trust —
  `_verify_fill` only ever sees a vetted reference.
- **Verification:** `tests/test_execution.py::TestReferenceVelocityClamp`
  (8 tests: first-trusted, slow-drift, burst-rejected, same-snapshot,
  catch-up, unwind-admitted, hash-covered, bad-config) — 103 passed in file
- **Source:** `docs/stealables-2026-09-repos.md` Tier D, `docs/freqtrade-stealables.md:Free10`

---

## Phase 3 — Observability & Metrics (P2): What Gets Measured Gets Managed

### 3.1 — External loop-stall watchdog (S1)
- **Status:** DONE 2026-10-13 — `metrics.check_loop_stall(max_age_s)` runs in the watcher (API) process against the persisted heartbeat: fresh→False/gauge 0.0, stale→True/gauge 1.0 (`tars_loop_stopped_contributing_condition_active`), never-started→None/gauge untouched. Wired into `/health` (`LOOP_STALL_MAX_AGE_S`, default 1800s = 2x scheduler interval). 4 new tests in `TestExternalStallWatchdog` (29/29 file green). In-loop `LoopWatchdog` transitions were already covered.
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
- **Note 2026-10-14:** `scripts/check_release.py` now covers the MECHANICAL half (gitleaks-or-visible-skip, full pytest, manifest doctor, hermetic artifact round-trips, printed manual remainder; first certification 953 passed). The specified half — separate runner/evaluator with signed bundles — remains open.
- **Problem:** No independent verification means no release. Self-validation is banned.
- **Fix:** Model eval: runner and evaluator separate; Ed25519 signatures over subject + runtime + receipts + rubric judgments
- **Verification:** Signed evaluation bundle verified by independent party
- **Source:** `docs/claude-ads-stealables.md:CA13`, `AGENTS.md`

### 3.7 — Deterministic scoring engine (CA6)
- **Status:** DONE 2026-10-07 — versioned `SCORING_PROFILE` (v1) in
  `src/execution/risk_gate.py`: whole-point weights (daily_loss 30,
  trailing_drawdown 25, cooldown 15, leverage 15, confidence 15) totaling
  exactly 100, enforced by `validate_scoring_weights` (rejects drift,
  bool-as-weight, negative, non-numeric) hard-failed at `RiskGate`
  construction; `score_order` computes from the profile (bit-identical
  math, pinned). Exported via `src/execution/__init__.py`.
- **Verification:** `tests/test_risk_dynamic.py::TestScoringProfileInvariant`
  (5 tests: totals-100, drifted/bool/negative/stringified rejected,
  construction fails on tamper, scores unchanged) — 185 passed with
  `test_execution.py` + `test_risk_gate_properties.py`
- **Source:** `docs/claude-ads-stealables.md:CA6`

### 3.8 — Mutation gate (CA7)
- **Status:** DONE 2026-10-07 — `src/mutation_gate.py`: proposal envelope
  (idempotency key; duplicate propose returns the existing proposal)
  over the real control-plane surfaces (`kill_switch` activate/deactivate
  both layers with snapshot rollback; `risk_params.set` /
  `asset_cap.set` via `OnchainLogger` with operator USD ceilings, rollback
  explicitly IRREVERSIBLE since the chain forbids loosening).
  Lifecycle preview (read-only diff) → approve (ceilings + recorded
  self-approval) → apply (once; replays cached receipt) → verify
  (re-read match, mismatch = FAILED) → rollback; every transition audited
  (`mutation_*` records, PROOF_EXECUTION). Existing endpoints untouched
  (their pinned tests stay green); the envelope is the operator path with
  real appliers. Per-trade counter increments deliberately ungated (they
  are the audited activity, not config changes); curator switches already
  carry their own allowlist/cooldown/revert lifecycle.
- **Verification:** `tests/test_mutation_gate.py` (12 tests: preview purity,
  ceiling reject, once-only apply + replay, approval required, verify,
  failure + audit, kill-switch rollback, tightening irreversibility,
  duplicate-key, unknown refs, self-approval flag, full audit trail)
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
- **Status:** VERIFICATION DONE 2026-10-13 — pipeline existed (fetch ~60-70 symbols, build with embargo assertion + baselines in `main()`); added `tests/test_carry_dataset.py` 6/6 on synthetic CSVs: per-8h normalization, trailing exclusivity, feature-stability-under-append (NaN-aware), y_win bar both sides. OPEN FINDING (not fixed — needs ML owner): tail rows carry TRUNCATED 7d labels (skip fires only on zero forward data; e.g. last row's "7d" label sums 1 period), pinned by `test_tail_labels_truncated_documents_behavior`. Changing it alters the validated model's training set.
- **Problem:** The 2y funding input is Binance USDT-perp funding as a cross-venue proxy for OKX. OKX public funding history caps ~3 months.
- **Action:** `scripts/fetch_carry_data.py` extended to 70 symbols; `scripts/build_carry_dataset.py` built `data/carry/dataset.csv` (52,320 rows × 29 cols). Re-run on extended tier.
- **Verification:** Embargo assertion, zero/persistence RMSE baselines, `y_win` rate verified
- **Source:** `docs/DATASETS.md`, `docs/ML_ROADMAP_REVISED.md`

### 4.5 — Feature-drift hard-fail on model reuse
- **Status:** DONE 2026-10-07 — `ml/carry_inference.py`: `FeatureDriftError`
  (RuntimeError, flows into the S5 `ml_unavailable:` degradation path);
  `validate_feature_schema` (non-empty unique string list) enforced at load;
  `prepare_features_for_inference` raises on missing/None/non-numeric/bool
  keys (no more silent zero-fill — the live path passed 6 keys against a
  26-col model); NaN passes through (native to LightGBM, stop faking
  completeness); `predict_q20/q50` width-checked + missing-model raises
  instead of predicting zeros; absent artifacts raise instead of warning;
  deleted the stale 22-col inference `FEATURE_COLS` (training list + 26-col
  artifact are the single source). Verified live: same 6-col call now
  yields NEUTRAL + `ml_unavailable:FeatureDriftError`, never a zero-fed
  LONG. Re-exported via `src/ml_inference.py`.
- **Verification:** `tests/test_ml_inference.py` (23 tests, hermetic —
  models/ is gitignored so artifact checks skip when absent; includes
  train↔artifact AST parity + live-row-gap documentation) — 31 passed
  with `test_ml_degradation.py`, zero breakage
- **Source:** `docs/freqtrade-stealables.md:Free2`, `docs/MATH_CONCEPTS.md:5`

### 4.6 — `do_predict` inference shield (per-row novelty gate)
- **Status:** DONE 2026-10-07 — `ml/carry_inference.py::do_predict` returns
  an int flag array (1 ok / 0 shield / 2 expired): non-finite rows always
  shield; optional `(lo, hi)` bounds shield out-of-range rows (no training
  stats ship yet — without bounds only finiteness gates, documented in the
  docstring, with the stats sidecar assigned to 4.9); explicit
  `model_trained_at` + `max_model_age_s` expire all rows (no mtime inference
  — checkout noise lies; absent timestamps never read as fresh OR expired).
  Shape drift raises (caller bug, not novelty). `predict_carry_decision`
  shields before the model (no prediction made, NaN q20, eligible False);
  `CarryDecision` + both compat shims forward `shielded`/`novelty_flag`
  (getattr-guarded so old test doubles read as unshielded);
  `ml_funding_carry_signal` degrades shielded rows as
  `ml_shielded:novelty_flag=N` (S5), which the package gate already blocks on.
- **Verification:** `tests/test_ml_inference.py::TestDoPredictShield` +
  strategy gating tests (14 tests: finiteness, batch, bounds, expiry,
  width-raises, shield-never-reaches-model, clean-row eligible, strategy
  degrades on shield, absent-keys backward compat) — 45 passed with
  `test_ml_degradation.py`, zero breakage
- **Source:** `docs/freqtrade-stealables.md:Free3`, `docs/ika-stealables.md:117-133`

### 4.7 — Isotonic calibration + decile plot
- **Status:** DONE 2026-10-07 — hardened `src/validation.py::
  isotonic_calibration(q_pred, y_actual, tau=0.2)` from an untested,
  unwired helper with a silent `{"error"}` fallback into a verdict gate:
  monotone flag over decile actual means (the v2 flat-curve detector),
  size-weighted ECE + max gap, quantile coverage vs tau with coverage_ok,
  remainder-dealt deciling, NaN/inf/length/n<10 fail-closed raises (no
  silent dicts), missing sklearn raises. Still unwired to any live model
  (none exists post-falsification) — the gate is ready for the M2
  calibration report when one does.
- **Verification:** `tests/test_validation.py::TestIsotonicCalibrationGate`
  (9 tests: calibrated pass, v2-flat monotone fail + huge ECE, coverage
  miss flagged, decile structure incl. indivisible n, 4 bad-input raises)
  — 26 passed in file, zero breakage
- **Source:** `docs/MATH_CONCEPTS.md:5`, `docs/ML_ROADMAP_REVISED.md`

### 4.8 — Durable learning-state store (I12)
- **Status:** DONE 2026-10-07 — train/inference wired through a metadata
  sidecar (the `LearningStore` cycle-Merkle shape did not fit carry
  artifacts, so the durable per-training record lives next to them):
  `ml/train_carry.py::build_model_metadata` (pure: schema, trained_at,
  n_rows, symbols, dataset sha256 best-effort, git-sha-or-unknown) written
  as `models/carry_quantile/meta.json` on `--save`;
  `ml/carry_inference.py` loads it best-effort (absent/malformed pre-sidecar
  artifacts load fine, age unassessed — never bricks), refuses on
  sidecar↔artifact schema mismatch, exposes `get_model_metadata()`, and
  feeds `trained_at` into the 4.6 expiry shield (`MODEL_MAX_AGE_S` = 30d
  default, per-call override). A retrained model restarts its own clock;
  until one exists, age stays honestly unknown.
- **Verification:** `tests/test_ml_inference.py::TestModelMetadataSidecar`
  (10 tests: load/expose, mismatch refuses, absent/malformed tolerated,
  expired shields flag-2, fresh proceeds, override respected, no-meta
  never expires, builder shape + honest-absent) — 47 passed in file
- **Source:** `docs/PENDING_TASKS_REPORT.md:31`, `docs/oobe-sap-stealables.md:340-359`

### 4.9 — Data-drawer persistent artifact store
- **Problem:** Per-training artifacts live nowhere persistent.
- **Fix:** Adopt freqtrade Free1 data-drawer pattern: `models/<identifier>/sub-train-<PAIR>_<timestamp>/` with `*_model.joblib`, `*_metadata.json`, `*_feature_pipeline.pkl`, `pair_dictionary.json`.
- **Verification:** `test_ml_inference.py` — model loads with metadata, feature drift detected
- **Source:** `docs/freqtrade-stealables.md:Free1`

### 4.10 — Funding-rate candle backtest realism
- **Status:** DONE 2026-10-07 — `src/multi_leg.py::accrue_funding`
  (direction-aware per-period accrual from actual rate history: short
  collects positive funding, long pays; negatives flip; empty history
  accrues 0.0; bool/string/NaN/inf/negative-notional/unknown-side raise)
  + `Package.funding_accrued_usd` (persisted, `.get` fallback keeps old
  package files loadable) + `MultiLegExecutionManager.
  record_package_funding` (short-side share only — deliberately no side
  parameter, since the package vocabulary cannot express open longs;
  backtests needing long legs call accrue_funding directly).
  Known remainder (documented, not hidden): no live position/hold tracker
  calls record_package_funding on a schedule yet — the economics primitive
  and its persistence exist, the periodic accrual loop does not.
- **Verification:** `tests/test_multi_leg.py::TestFundingAccrual`
  (direction, flip, empty, 9 malformed-input raises, accumulate+persist+
  reload, real Binance history match) — 37 passed in file
- **Source:** `docs/freqtrade-stealables.md:Free5`

---

## Phase 5 — Pooled Vault + Depositor UI (P1): Product Surface

### 5.1 — `TradingVault.sol` deployment and verification
- **Status:** LOCAL VERIFICATION DONE 2026-10-13 — `tests/test_trading_vault.py` 36/36 green (covers every criterion: DepositTooSmall, MAX_TVL, donation resistance, two-step withdrawal, deadline expiry, rate limit). NOT YET DEPLOYED to any chain. Recommendation: deploy to X Layer **testnet** first; mainnet only with live track record + NAV pipeline (5.2) live.
- **Fix:** Deploy to X Layer mainnet (chainId 1952). Verify ERC-4626 semantics, MIN_DEPOSIT/MAX_TVL enforcement, two-step withdrawal, settlement-window redemption.
- **Verification:** `tests/test_vault.py` — deposit reverts on `DepositTooSmall`, deposit reverts at `MAX_TVL`, share-price donation attack resistance, two-step withdrawal, deadline expiry, rate limit
- **Source:** `docs/DESIGN-external-vault.md`, `contracts/contracts/TradingVault.sol`

### 5.2 — Operator-attested NAV reconciliation
- **Status:** DONE 2026-10-13 — module (`read_vault_state`, `read_okx_balance`, `reconcile` with discrepancy + suggested attestation + honest read-error surfacing), endpoints (`GET /api/v1/vault/reconciliation`, `POST /api/v1/vault/attest`), and `tests/test_reconciliation.py` 6/6 green. Live attestation awaits vault deployment (5.1 remainder).
- **Problem:** `totalAssets` on the vault must equal what the agent actually holds in its OKX account. v1 uses operator-attested balance.
- **Fix:** `src/reconciliation.py` exists — wire it to the agent's `attestTotalAssets` call. Ensure the attestation is clearly labeled "value reported by the operator, auditable on-chain" in the UI.
- **Verification:** `test_reconciliation.py` — discrepancy detected and reported
- **Source:** `docs/DESIGN-external-vault.md:4.2`, `src/reconciliation.py`

### 5.3 — Wallet connect for real (depositor surface)
- **Status:** DONE 2026-10-13 — `static/depositor.html` calls `vaultContract.deposit/requestWithdraw/finalizeWithdraw` with approve flow + tx states (Reown AppKit connect). Was wired; doc was stale. Full end-to-end awaits vault deployment (5.1 remainder).
- **Problem:** The depositor page's connect button is a placeholder.
- **Fix:** Wire wagmi/viem (chain 1952) to `TradingVault.deposit` / `requestWithdraw` / `finalizeWithdraw`.
- **Verification:** Deposit/withdraw call real contract; tx states surfaced honestly
- **Source:** `docs/DESIGN-external-vault.md:5.4`

### 5.4 — Depositor surface: five-screen progressive disclosure
- **Status:** DONE 2026-10-13 — all six screens existed as sections in `static/depositor.html`; added `/depositor/{hero,stats,deposit,withdraw,my-position,verify}` deep-link routes (each serves its section via tab-activate + scroll, unknown screens 404, `include_in_schema=False` so the manifest doctor holds) + `tests/test_depositor_screens.py` 9/9 green. Also fixed Reown X Layer chain ID 1952→195 (entry said testnet, config said mainnet).
- **Fix:** `/depositor/hero`, `/depositor/stats`, `/depositor/deposit`, `/depositor/withdraw`, `/depositor/my-position`, `/depositor/verify`
- **Verification:** Each screen independently functional
- **Source:** `docs/complimentary-seedless-patterns.md:47-55`

### 5.5 — Rate limiting on withdrawals
- **Status:** DONE 2026-10-13 — `WithdrawalRateLimited` already in `TradingVault.sol`; `test_request_withdraw_rate_limited` green in `tests/test_trading_vault.py` (36/36). Was implemented with the contract; doc was stale.
- **Problem:** No per-depositor withdrawal rate limit (v1.1).
- **Fix:** Add `WithdrawalRateLimited` error; max 1 withdrawal per 8h per depositor in `TradingVault.sol`.
- **Verification:** Contract test — rate-limited withdrawal reverts
- **Source:** `docs/complimentary-seedless-patterns.md:96-98`

### 5.6 — On-chain reputation/capability registry (I1)
- **Status:** DONE 2026-10-13 (code + tests; DEPLOYMENT PENDING key call) — new `contracts/contracts/AgentRegistry.sol` (self-registration keyed by msg.sender, update/deactivate, `reputation()` view over `TradeAuditTrail.getAgentDailyStats` + kill-switch flag; audit-trail contract untouched); `tests/test_agent_registry.py` 9/9; registry pointer on the discovery card (honest-null until deployed); `compile_contract.py` + `deploy.py` extended (`AgentRegistry` target, chain ID now read from RPC instead of hardcoded); `REGISTRY_CONTRACT_ADDRESS` documented in `.env.example`.
- **Problem:** `manifest.json` is a static file with no reputation, pricing, or discovery index.
- **Fix:** Extend `TradeAuditTrail.sol` or add `AgentRegistry.sol` on chain 1952 with signed capability/pricing/reputation surface. Feed from `TradeAuditTrail.sol`'s `dailyLoss`/`dailyTrades`.
- **Verification:** Discovery card served; on-chain registry queryable
- **Source:** `docs/oobe-sap-stealables.md:37-58` (I1)

### 5.7 — x402 pricing tiers + spend caps + rate limiting (I3 + I6)
- **Status:** DONE 2026-10-13 — all implemented in `main.py` (tiered card, `X402_MAX_USD_PER_CALL` fail-closed cap, `/api/v1/estimate`, global + per-tier per-IP token buckets); `tests/test_pricing.py` 9/9 green covering card/estimate/cap/buckets. Was implemented; doc was stale.
- **Problem:** x402 middleware is binary (paid route or not); no pricing model, spend cap, or rate limit.
- **Fix:** Tiered price card: Free (`/health`, `/manifest`, `/kill-switch`), Micro (`/positions`, `/funding-arb-status`), Premium (`/hire`, `/trade`, `/vault/*`). Add per-call max-usd cap + estimate endpoint + token-bucket rate limiting.
- **Verification:** Price card served; rate limiting enforced; spend cap honored
- **Source:** `docs/oobe-sap-stealables.md:78-153` (I3, I6)

### 5.8 — Discovery card + MCP surface (I4)
- **Status:** DONE 2026-10-13 — full surface served (agent-card, x402, /docs, openapi.json; card entry points resolve; openapi covers /hire). Card/tools/payment shapes already pinned in `test_manifest.py::TestAgentDiscovery`; added `tests/test_discovery_surface.py` 3/3 for the complete set.
- **Fix:** Serve `/.well-known/agent-card.json` + `/.well-known/x402` + `/docs` + `openapi.json`. Another AI agent can discover, read the price card, and call `/hire`.
- **Verification:** Discovery card served at well-known URL; valid JSON structure
- **Source:** `docs/oobe-sap-stealables.md:101-118` (I4)

### 5.9 — Two-tier audit retention: seal-on-settlement (I5)
- **Status:** DONE 2026-10-13 — design already existed (JSONL working buffer, Merkle seal per cycle, transients never promoted); built the missing piece `AuditLog.rolloff(keep_cycles)` (atomic, seals never dropped, recency-based keep incl. unsealed tails) + seal-membership and 4 rolloff tests in `test_audit_trail.py` (12/12 green). On-chain gating (executed-only logDecision) covered by the signature-roundtrip suite.
- **Problem:** On-chain `logDecision` writes every decision (gas-costly). JSONL is the working buffer. Need explicit two-tiering.
- **Fix:** On-chain for sealed/permanent records (executed decisions), JSONL for working buffer that rolls off. Seal on settlement trigger.
- **Verification:** Only executed decisions appear on-chain; JSONL working buffer rolls off correctly
- **Source:** `docs/oobe-sap-stealables.md:120-136` (I5)

---

## Phase 6 — API & Developer Experience (P2)

### 6.1 — Schema-validate + auto-generate `manifest.json` (I7)
- **Status:** DONE 2026-10-13 — `python scripts/check_manifest.py` reports healthy; `tests/test_manifest.py` green (doctor, schema, bidirectional route agreement). Was implemented; doc was stale.
- **Problem:** `manifest.json` is hand-maintained with "unknown endpoint" descriptions. Drift risk.
- **Fix:** Generate from FastAPI OpenAPI spec + CI check validates against schema + endpoint-exists check.
- **Verification:** `python scripts/check_manifest.py` passes; every documented endpoint exists
- **Source:** `docs/oobe-sap-stealables.md:154-169` (I7), `scripts/check_manifest.py`

### 6.2 — `src/mcp_bridge.py` wire to a real consumer (I13)
- **Status:** DEFERRED 2026-10-13 (correctly open, not buildable) — bridge is complete + tested (8/8 `test_mcp_bridge.py`), zero live-path consumers by design: the module docstring forbids wiring without an external research input, and none exists yet. Wiring now would manufacture the dead code it guards against. Re-open when a research input arrives.
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
- **Status:** DONE 2026-10-07 — `src/risk_defaults.py` (stdlib-only):
  `RISK_DEFAULTS` (14 knobs, values == long-standing behavior) read by
  `RiskGate.__init__` defaults AND `Settings` field defaults (the old
  literals are gone from both), plus strict unit converters
  (`slippage_pct_to_fraction` / inverse, bool/string/NaN/negative refuse).
  `agent.py` dispatch collar converts the LIVE gate value through the
  helper (no inline `/100`); `main.py::_make_risk_gate` now also passes
  slippage + velocity from settings (previously gate-default-only, silent
  — defaults identical, behavior unchanged); `.env.example` documents the
  two newly-wired knobs. Step()'s tighter 0.003 default kept as deliberate
  policy (dispatch binds first), pinned by test to never exceed the gate
  collar — unifying it upward would loosen dispatch 3x and needs an
  operator decision, not a refactor.
- **Verification:** `tests/test_risk_single_source.py` (12 tests: table
  pins behavior, gate attrs match, settings fields match statically,
  roundtrip, 6 coercion rejections, live-collar equality, step-tightness
  direction) — 28 passed with preflight + manifest suites
- **Source:** `docs/ika-stealables.md:188-204` (S9)

---

## Phase 7 — Community & Bounty (P3): RPC Fast + Panta

### 7.1 — RPC Fast Infrastructure Sidetrack
- **Status:** DONE 2026-10-09 (code; community tasks + live key remain) —
  `src/solana_rpc.py`: `fetch_solana_market_snapshot()` (SOL/USD from the
  on-chain Pyth push account + slot, magic-checked parser, any failure →
  typed `RpcFastError` so callers keep the OKX path), usage/latency metrics,
  `GET /api/v1/rpc-fast-status` (honest-null unconfigured; grpc honestly
  `not-implemented` — Yellowstone needs a heavier client than stdlib HTTP).
  Settings + `.env.example` carry the 5 keys. Unconfigured by default:
  zero behavior change without keys.
- **Remaining (operator-side):** RPC Fast key/plan, community tasks
  (posts, follows, TG/Discord), dual hackathon entry.
- **Verification:** `tests/test_rpc_fast.py` (5 tests: unconfigured raises,
  magic gate, success shape + counting, error fallback + counting, route
  honest-null) — green
- **Source:** `docs/RPC_FAST_GAP_REPORT.md`

### 7.2 — Panta API Sidetrack
- **Problem:** TARS has no prediction-market ability. Panta integration needed for the bounty.
- **Status:** DONE 2026-10-09 (client + agent wiring; live quote/demo still operator-side) —
  all 8 client paths rewritten to the published API (verified page-by-page
  against docs.panta.market: the first revision guessed 6 wrong paths and
  omitted wallet/build/submit entirely). `catalog`→`GET /markets/`,
  quote→`POST /primaryorderquote/` (+required wallet), added
  `primary_buy_build`/`primary_buy_submit`, positions→query param,
  claims→singular `/claim/...`, report→`POST /trades/` with real fields,
  full create quote→build→register trio; `PantaError` carries API codes;
  agent `_panta_phase` takes wallet in the asset encoding and the
  unwritable create-via-asset path retired with an explicit error.
- **Remaining (needs operator key + wallet):** live quote test with real
  `PANTA_API_KEY`; wallet signing handoff; risk-gate + onchain logging for
  Panta fills; demo + dual submission (Colosseum + Earn).
- **Verification:** `tests/test_panta_client.py` (14 tests, MockTransport —
  every path/field + error codes + agent wiring) — green
- **Source:** `docs/panta-integration-report.md`

### 7.3 — Solami bounty ($3k, due ~Oct 13)
- **Status:** CODE 2026-10-09 (key + Blur docs still operator-side) —
  `fetch_via_solami()` in `src/solana_rpc.py` (same Pyth+slot snapshot
  core as RPC Fast, `source=solami` in metrics; settings
  `solami_api_key/rpc_url/plan`, official Edge shape
  `rpc.solami.fast/sol?api_key=` assembled from the key unless an
  explicit URL is set). Blur firehose client BUILT 2026-10-09
  (`src/solami_blur.py`: documented `/data/subscribe` shapes only —
  token_create/pool_create/graduation + launches/graduating/graduated
  snapshots, narrow subscribe + bounded backfill, connect budget,
  unknown shapes skipped-and-counted, DataApi-less keys refused
  client-side): 5 hermetic tests. NOT live-verified (needs the DataApi
  role on the operator key + a real socket run).
  no documented REST surface found (only gRPC/WS streams per the Rust
  SDK notes) — building the Blur client needs the endpoint docs from
  the operator, not guesses. Trial/roles also unverified (dashboard key
  shows no roles; Blur needs the Data API role).
- **Remaining (operator-side):** confirm trial active, add Data API role
  to the key, live socket run to verify shapes, demo + submission.
- **Verification:** `tests/test_rpc_fast.py` (+2 Solami tests: unconfigured
  raises, fetch counts source=solami) + `tests/test_solami_blur.py`
  (5 tests) — green

---

## Phase 8 — Research & Future Propositions (P4)

### 8.1 — Tokenized stocks (Book 2, gated on ICE-OKX JV)
- **Trigger:** ICE-OKX JV ships NYSE tokenized equities on an OKX surface (venue gate first).
- **Status:** Research complete. NYSE SR-NYSE-2026-17, Securitize+Jump+Jupiter, Robinhood Chain, ICE-OKX JV all verified. BONER/HIMS proved wrapper premium collapses to NAV.
- **Action:** Only graduate to v1 candidate if ICE-OKX JV delivers. Keep Book 2 until carry quarter is proven.
- **Source:** `docs/stocks-onchain-research.md`, `docs/PATH_FORWARD.md:5`

### 8.2 — Memecoin satellite engine (Book 2, gated on G0-G3)
- **Status:** Paper-only v1 BUILT 2026-10-07 (`src/meme_satellite.py`):
  scream filter (11 checks, SKIP default on missing data), structural entry
  on graduation confirmation, deterministic pivot exits (rug/time/peak/
  tape/tax), RUG sentinel (LP drain + collapse velocity), time box,
  post-model size clamp, cost-realistic paper blotter, seeded determinism.
  No keys, no network, no swap path — G1 paper loop can start once a
  snapshot feed (Bitquery/poll) is wired by the operator. Gates unchanged:
  G0/G2/G3 still closed; Book-1 wall intact.
- **Feed adapter** (`src/meme_feed.py`, 2026-10-07): Helius RPC →
  TokenSnapshot (mint authorities from the 82-byte SPL layout, top-holder
  share from largest accounts, operator extras for age/liquidity/tax/
  creator/graduation, unobserved stays None). Ordered credentials
  (explicit → HELIUS_RPC_URL → HELIUS_API_KEY), typed FeedError, bad mints
  skip without aborting the round. Discovery is operator watchlist —
  no sniper polling, per the slower/pickier doctrine.
- **Discovery + enrichment** (`src/meme_discovery.py`, 2026-10-07):
  DexScreener free keyless API, verified live — `token-profiles/latest/v1`
  yields fresh `...pump` mints (pump.fun runs no official launch API; its
  board is unofficial/gated), `/latest/dex/tokens/{mint}` enriches
  (deepest SOL pool wins: liquidity, volume, txns, age from oldest pair,
  graduation = liquid pool on raydium/orca/meteora/jupiter). Failures
  raise DiscoveryError (zero candidates, never guesses). Live-verified:
  BONK/WIF snapshot→verdict on mainnet; `scripts/meme_snapshot.py`
  `discover` runs the full paper round.
- **Design:** Rule/novelty archetype. Scream filter (reject ~95% pre-buy), structural entry on survival, deterministic pivot exits, RUG sentinel, time-boxed holds, size clamp post-model.
- **Verification:** `tests/test_meme_satellite.py` (33 tests) +
  `tests/test_meme_feed.py` (10 tests, faked transport) +
  `tests/test_meme_discovery.py` (9 tests, faked HTTP) +
  `tests/test_meme_execute.py` (13 tests, faked HTTP + stub signer) +
  `tests/test_meme_venue_costs.py` (7 tests) — full suite green.
  Live-verified 2026-10-09: real `/order` quote against mainnet returns
  typed `Insufficient funds` for the unfunded Book-2 wallet (path +
  error envelope proven, $0 moved).
  Tiny-live execution (`src/meme_execute.py`, 2026-10-09): Swap V2
  order→sign→execute with paper-by-default (confirm string required),
  cost-hurdle guard, session caps, stub-injectable signer.
  **FIRST LIVE TRADE EXECUTED 2026-10-10**: 0.005 SOL → 0.550948 USDC,
  signature `3Xqb5ja2…ZT`, Book-2 wallet `38hpURXX…z2MbzY`, Jupiter
  Swap V2 managed landing. Full pipeline proven end-to-end on mainnet:
  funded wallet → quote (fees from response, no flat proxy) → hurdle
  ($0.0055 costs vs $0.0551 allowed) → solders signing → execute →
  on-chain balance verification. The $5 test budget is live.
- **Oct 13-15 hardening sprint (live VPS paper loop, ~3.7k screens):**
  evidence-ordered scream filter (holder floor, graduation-first,
  creator-history, concentration, taxes, liquidity; LP/authorities/age
  demoted to notes per grounded research), RugCheck feed (creator +
  rugged verdict), adaptive sizing (0.1% liq / 1% vol / $5 dust),
  deployer-history bootstrap (`src/meme_history.py`), bundle graph v1
  (`src/meme_bundle.py`, 1-hop, fail-closed incomplete), satellite HALT
  kill-switch, survivor observer, `--diagnose` instrumentation. G1 still
  open: zero entries (creator-history supply is the single blocker).
- **Oct 23 (window-4 prep, no rule change in the frozen window):**
  - G1 report now attributes net PnL by exit class:
    `src/meme_g1.classify_exit_reason` (prefix match mirroring the loop's own
    `str(reason).upper().startswith("RUG")`) + `build_attribution` +
    `scripts/meme_g1_report.py --attribution`. Missing/bool/NaN/inf `net_usd`
    is counted as unpriced and excluded from the average, never read as
    break-even. Window-3 re-run: 56 exits −$394.79 → rug 31 trips −$468.02
    (avg −$15.10) vs pivot 25 +$73.22 (avg +$2.93).
    Regression: `tests/test_meme_g1.py` (26 tests).
  - Confirmation stage built on branch `wip/restart-4-confirmation-stage`
    (UNMERGED, `confirm_stage=False` default): a pass parks a pending entry,
    the next LP observation produces a second sample, `liquidity_verdict`
    decides; unarmed cannot reject by construction. Guards: same-cycle dedupe
    (MIN_SPAN_S), 900 s episode reset, `entry_pending_expired` sweep, caps
    re-checked at the real entry moment, pending state persisted in
    `positions.json`, `confirmation_samples` recorded on every confirmed
    entry, and the runner re-enriches pending mints (discovery alone starves
    the stage). Regression: `tests/test_meme_confirm_stage.py` (11 tests).
- **Source:** `docs/alpha-mode-memecoins.md`

### 8.3 — Hummingbot venue-aware execution
- **Trigger:** After Book 1 validated quarter → alts same venue.
- **Steal:** `slippage = f(size, liquidity)` per venue + MEV/Jito handling + `inventory skew` → replace flat `max_slippage_pct` with venue-aware model.
- **First slice PORTED 2026-10-07** (Stockulus → satellite):
  `src/meme_venue_costs.py` carries the DBC fee schedules
  (calm linear 120→100bps / stress exponential 900→100bps, with regime
  mults 1.0/1.2/2.0) selected by the ported HMM state, with AMM impact
  as size/liquidity; `PaperBlotter.record_exit` accepts venue slippage
  (flat proxy default unchanged). Mainnet schedules replace the devnet
  numbers when the satellite leaves paper.
- **Verification:** `tests/test_meme_venue_costs.py` (7 tests)
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
- **Status:** DONE 2026-10-23 (all resolvable items closed; one decision left
  with the operator). Item by item:
  - `requirements-ml.txt` — **already existed and is tracked** (`xgboost`,
    `lightgbm`, `torch`, `scikit-learn`, `pandas`). The dev-only trio the CI
    needs lives in `requirements-dev.txt` (f803208) so Vercel stays slim.
  - Stale test counts in `twitter_thread.md` / `docs/audit-trail-trader-diagram.mmd`
    — **verified absent**: neither file states a test count any more.
  - `.mmd` rendering claim — **already corrected in place** by the 2026-10-02
    note ("no SVG/PNG/Excalidraw renderings are checked in; render from the
    `.mmd` source on demand").
  - `ML_ROADMAP_REVISED.md` — never tracked (local-only, `.git/info/exclude`);
    the carry experiment's falsification is recorded in
    `docs/falsifications-course-of-action.md`, which is the tracked authority.
  - Dead weight — `-p/` did not exist; `docs/design/waitlist-confirmation-email.html`
    was untracked+excluded (feature withdrawn) and is now deleted from disk;
    `scripts/train_carry_model.py` no longer contains a `NotImplementedError`
    placeholder (already replaced by the real training path).
  - 13 untracked scratch files from an earlier doc audit
    (`check_refs*.py`, `missing_*.txt`, `compare_result.txt`, `HTTP`, …) deleted.
  - **Still open (operator decision, not code):** dead-code policy for the
    zero-address fallbacks, `fill_timeout_cycles` and `get_nonce()`. These are
    dormant-by-design guards; the question is whether "dormant but correct"
    is allowed to stay, which is a policy call, not a cleanup.
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
- **Current:** 1143 passed, 0 failed, 0 errors (2026-10-23, full
  `python -m pytest tests/ -q`; the earlier 16 Windows tmp errors and 3 env
  401s are gone).
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

Re-checked 2026-10-23 against the running system — `/manifest` and the agent
card, X Layer testnet RPC (`eth_getCode`), the VPS service, and the test suite.
The code is the source of truth; this table is a map, and it had drifted far
enough that some rows said "missing" about things that ship and "working" about
things that do not. Rows carrying ⚠️ are older judgements that have **not** been
re-verified — read them as unverified, not as current fact.

| System | Status |
|--------|--------|
| Satellite loop (`src/meme_loop.py`, `scripts/meme_runner.py`) | ✅ Live on the VPS as `tars-satellite`, **paper only**. G1 gate unmet: no live track record |
| Satellite entry accounting (`spent_usd` residue) | ✅ Fixed 2026-10-23 (`af80351`); proven in production: entry → rug exit → `spent_usd` back to exactly 0.0 |
| Core trading loop (`src/agent.py::run_trading_cycle`) | ✅ Working |
| Risk gate (`src/execution/risk_gate.py`) | ✅ Working (non-overridable) |
| Kill switch | ⚠️ Gate logic works; engaged state is in-process only, not durable |
| Onchain audit (`src/audit_logger.py` → `TradeAuditTrail.sol`) | ✅ Deployed and logging on X Layer testnet (`0x6019b9…`) |
| TradingVault.sol | ✅ Deployed on X Layer testnet (`0x747114…`) |
| TreasuryMultisig.sol | ⚠️ Written, not deployed |
| AgentRegistry.sol | ⚠️ Written, not deployed — the onchain registry pointer is still null |
| Multi-leg execution (`src/multi_leg.py`) | ⚠️ Partial-fill bug (D1), unchanged since 2026-08 |
| Signal engine (`src/signals.py`) | ✅ Imports clean (the old `DBCCurveType` crash is gone) |
| Data integrity gate (`src/data_integrity.py`) | ✅ Working |
| Curator (`src/curator.py`) | ✅ Working (auto-revert needs D7 fix) |
| Validation gate (`src/validation.py`) | ✅ Working |
| Audit trail local JSONL (`src/audit_trail.py`) | ✅ Working |
| Metrics (`src/metrics.py`) | ✅ Working (needs S3 expansion) |
| Learning store (`src/learning_store.py`) | ✅ Working |
| MCP bridge (`src/mcp_bridge.py`) | ✅ Working (unwired) |
| Panta client (`src/panta_client.py`) | ✅ Rewritten to documented API (14 tests) |
| Signer interface (`src/signer.py`) | ✅ Working (D10 ready) |
| Reconciliation (`src/reconciliation.py`) | ✅ Working |
| Alerting (`src/alerting.py`) | ✅ Working |
| Hire contract (`POST /hire`) | ✅ Accepts every shape it documents: `own_account`/`demo`/`live`/`data`, canonical row or `{"data": […]}` envelope; pinned by `tests/test_hire_contract.py` |
| x402 pricing | ✅ Two tiers in `PRICED_ROUTES` (`premium` $0.50, `micro` $0.01); ⚠️ **not enforced** — `PAY_TO_ADDRESS` is unset, so priced routes are served free and the card reports `enforced: false` |
| Solana data path (`src/solana_rpc.py`) | ⚠️ Read-only SOL price/slot over RPC Fast; unconfigured and honest-null. No Solana trading path |
| RPC Fast integration | ⚠️ Client + status surface exist (`/api/v1/rpc-fast-status`); no key configured |
| Depositor UI | ⬜ Static `static/depositor.html` only |
| Wallet connect (real) | ❌ Placeholder |
| Persistent host | ⚠️ Split: API on Vercel serverless, satellite on a provisioned VPS |
| Test suite | ✅ 1172 passed |

Still carried from 2026-08 and **not** re-checked: volume-limit enforcement,
fat-finger on market orders, allowlist semantics, consensus quarantine,
feature-drift hard-fail, `do_predict` novelty gate, isotonic calibration,
data-drawer persistent store. Assume stale until verified.

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
