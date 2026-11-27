# fpz live-wiring spec — funding_persistence_z into the agent path

**Status:** DRAFT (write-only spec, no code).  
**Trigger:** Write now, build later — only inside a declared VPS restart window, and only if the paper cohort (≥30 trades, net carry > $0 after costs, no kill anomalies) reaches a PASS verdict. This spec is the contract that build implements; it is not permission to build.

**Source of truth for the validated edge:** `scripts/paper_trade_sol.py` — `funding_persistence_z` on SOL-USDT-SWAP, params locked at `z≥1.5 / window=5 / frac=0.8 / mild=0.0003`, costs 5 bps fee + 3 bps slippage per side (~16 bps round trip), break-even ≈3.05 bp/period. **Never re-tuned on paper results.**

---

## 1. Problem statement

The validated edge already exists in two places that do not agree:

| Surface | How SOL is treated | Why it diverges from the validated harness |
|---|---|---|
| `scripts/paper_trade_sol.py` (validated) | Standalone signal, direct exit, per-side cost, break-even floor | Deliberately avoids the agent cycle |
| `src/agent.py` `_generate_signals()` (line 485) | `funding_persistence_z_signal` is PRIMARY for SOL, `funding_rate_signal` secondary | Ensemble then dilutes it with `funding_rate`, `mean_reversion`, `ml_funding_carry` |
| `src/agent.py` `_funding_arb_opportunity()` (line 652) | Takes PRIORITY over all directional signals — runs delta-neutral long-spot/short-perp package instead | Hijacks SOL exactly when funding is extreme, which is precisely when fpz fires SHORT (naked directional risk, not hedged) |

Three wiring problems the spec must answer:

1. **Signal priority.** For SOL, is `funding_persistence_z` the sole decision input, or does the ensemble vote override it? The validated edge was standalone; ensemble dilution is the dilution risk the paper_trade_sol docstring names.
2. **Ensemble bypass.** Should fpz bypass `ensemble_signal()` entirely for SOL (direct signal → risk gate), or stay ensemble-member? The gate validated fpz at OOS Sharpe 2.206 as a single signal; ensemble behavior was not validated.
3. **Arb-package coexistence.** `_funding_arb_opportunity()` runs the delta-neutral package when funding ≥ `funding_arb_min_rate` (0.001 default) — the same regime where fpz's strongest SHORT signals fire. The two are mutually exclusive per asset per cycle (one trade per asset per cycle). Which wins, and under what funding conditions?

---

## 2. Scope

**In scope:**
- Wiring `funding_persistence_z` as the live signal input for SOL-USDT-SWAP inside `src/agent.py`'s `_process_asset` / `_generate_signals` path.
- Resolving priority among fpz, ensemble, `funding_rate`, and the funding-arb package for SOL.
- New or repurposed settings knobs (declared in `src/settings.py`, consumed by `src/agent.py`).
- Regression tests: signal priority, ensemble bypass, arb-package coexistence.
- Rollout: declared window, dry-run first, kill-switch interlocks.

**Out of scope:**
- Changing `scripts/paper_trade_sol.py` (the paper harness stays standalone; it is the verdict source, not the deploy target).
- Changing `funding_persistence_z_signal()` parameters (locked at z≥1.5/5/0.8/0.0003).
- Any live (DRY_RUN=false) execution before the gate verdict PASS.
- Multi-asset extension (BTC/ETH) — fpz is SOL-only per the do-not-extend rule.

---

## 3. Non-goals (anti-scope)

- No fpz wiring on BTC/ETH/other majors until the SOL cohort passes AND a separate gate clears.
- No re-tuning of `z_threshold`, `persist_window`, `persist_frac`, or `mild_threshold` based on live behavior.
- No bypass of `RiskGate` — every fpz order still passes `check_order`, the confidence floor, slippage collar, and onchain audit log.
- No removal of the funding-arb package for non-SOL assets.

---

## 4. Interface contract

### 4.1 Settings knobs (declared in `src/settings.py`, single table)

| Knob | Type | Default | Meaning |
|---|---|---|---|
| `fpz_live_enabled` | `StrictFlag` | `False` | Master switch. `False` = current behavior unchanged (fpz stays a signal member; no live wiring). `True` = fpz wired per this spec. Requires gate verdict PASS before flipping. |
| `fpz_sol_only` | `StrictFlag` | `True` | When `True`, fpz wiring applies only to SOL-USDT-SWAP. Other assets keep existing paths. |
| `fpz_bypass_ensemble` | `StrictFlag` | `True` | When `True`, for SOL the fpz signal goes straight to the risk gate (no `ensemble_signal()` dilution). When `False`, fpz is one ensemble member among many. |
| `fpz_confidence_floor_bps` | `PositiveInt` | `7000` | Minimum ensemble/fpz confidence for a SOL order (70%). Above the signal's own `is_tradeable` (6000) and the gate default. |
| `fpz_arb_package_priority` | `StrictFlag` | `False` | When `False` (default), fpz directional signal has priority over the funding-arb package for SOL. When `True`, the arb package keeps current priority (hijacks SOL at funding ≥ 0.001). |
| `fpz_arb_block_band_bps` | `PositiveFloat` | `150` | When `fpz_arb_package_priority=False`, the arb package is blocked for SOL when \|funding_rate\| ≥ this band (bps), i.e. exactly the extreme regime where fpz fires. 150 bps = 1.5%/8h, well above the mild ±0.0003 band. |

### 4.2 Signal flow (when `fpz_live_enabled=True`)

For each SOL-USDT-SWAP cycle:

```
fetch market data (perp + spot)
  │
  ▼
integrity gate (fail-closed) ──┐
                               ▼
                    _funding_arb_opportunity(SOL)
                               │
              ┌────────────────┴────────────────┐
              │ fpz_arb_package_priority=False  │ fpz_arb_package_priority=True
              │  (default)                      │  (current behavior)
              ▼                                 ▼
       block arb when \|funding\| ≥ band      arb runs package (hijacks SOL)
              │                                 │
              ▼                                 ▼
    funding_persistence_z_signal(SOL)      [no directional signal this cycle]
              │
    confidence ≥ fpz_confidence_floor_bps?
              │
          Yes ▼
              ▼
         RiskGate.check_order → onchain log → execute
```

For non-SOL assets: existing behavior unchanged regardless of `fpz_live_enabled`.

### 4.3 Confidence and sizing

- fpz signal confidence is computed by `funding_persistence_z_signal()` (0.72–0.92 mapped from z + persistence). No live modification.
- Order size: fractional Kelly via `_compute_order_size()`, curator `position_size_multiplier` applied, capped by `max_position_usd`.
- Entry price: `signal.entry_price` required (D3 fail-closed — no order without a signal-time price view for the pre-trade collar).
- Reduce-only: `False` for entries (position reduction via unwind path only).

### 4.4 Audit and logging

Every fpz SOL decision (pass or reject) is written to `AuditLog` with proof type `PROOF_DECISION`, event type `fpz_decision`, fields:
- `cycle_id`, `asset`, `strategy="funding_persistence_z"`, `direction`, `confidence_bps`, `funding_z`, `persistence`, `funding_rate`, `entry_price`, `size_usd`, `bypass_ensemble`, `arb_package_blocked`
- Rejections/abstentions record `rejected_by` with the layer that actually ended the cycle: `abstained` (fpz NEUTRAL — no trade by design) / `confidence_floor` / `curator_floor` / `safety_net` / `no_order` (D3, already positioned, zero Kelly edge) / `risk_gate` / `onchain_log`. Approved decisions record `rejected_by: null`.
- Integrity blocks are recorded by the generic `integrity_block` event instead — no fpz signal is generated in that case, so there is nothing fpz-specific to attribute. The arb-band refusal is reported as the `arb_package_blocked` boolean on the decision record (a package that never ran is not a rejected fpz order).

If `onchain_logger` is configured and not dry-run, the decision is logged to `TradeAuditTrail.sol` before dispatch (same hard gate as every other order).

---

## 5. Behavioral requirements

### 5.1 Signal priority (requirement SP-1)

When `fpz_live_enabled=True` and `fpz_bypass_ensemble=True`:
- For SOL-USDT-SWAP, the ensemble is NOT consulted for the fpz decision. The fpz signal (after its own internal z/persistence filter) is the sole directional input to the risk gate.
- `funding_rate_signal` and `mean_reversion_signal` and `ml_funding_carry_signal` may still be generated for observability but do not vote on SOL when bypass is on.

Rationale: the gate validated fpz standalone; ensemble dilution was not validated.

### 5.2 Ensemble bypass (requirement EB-1)

- `fpz_bypass_ensemble=False` restores current behavior: fpz is one ensemble member, ensemble vote decides direction/confidence.
- The bypass flag is per-process, not per-asset; it applies to SOL only when `fpz_sol_only=True`.

### 5.3 Arb-package coexistence (requirement AC-1)

When `fpz_arb_package_priority=False` (default after wiring):
- For SOL, the funding-arb package is blocked when `abs(funding_rate) >= fpz_arb_block_band_bps / 1e4`. This is the regime where fpz's strongest signals fire (z-extreme funding).
- Below the band, the arb package may run (delta-neutral, market-neutral) — no conflict with fpz's contrarian directional bets at moderate funding.
- When `fpz_arb_package_priority=True`, current behavior is preserved: arb package hijacks SOL at funding ≥ `funding_arb_min_rate`.

Rationale: the paper_trade_sol docstring states the arb package "hijacks SOL exactly when funding is high, which is precisely when the validated edge fires SHORT." Wiring fpz without resolving this reintroduces the exact conflict the harness avoids.

### 5.4 Risk gate (requirement RG-1)

- Every fpz order passes `RiskGate.check_order` unchanged: confidence floor (`min_confidence_bps` or curator override), slippage collar (`max_slippage_pct`), price freshness (`max_price_age_seconds`), position/daily limits, kill switch.
- `fpz_confidence_floor_bps` is an ADDITIONAL floor on top of the gate's own — never a replacement.
- Reduce-only enforcement unchanged; fpz entries are never reduce-only.
- Fail-closed: any missing input (price, funding history < 5 points) → NEUTRAL, no order, never a fabricated default.

### 5.5 Dry-run invariant (requirement DR-1)

- `DRY_RUN=true` (default): no real orders, no onchain transactions, no fee="0" simulation lies. fpz runs through the same code path with dry-run fills.
- `DRY_RUN=false` requires explicit `ALLOW_LIVE` interlock per `src/settings.py` (DRY_RUN must be set explicitly in env).

---

## 6. Rollout

All changes inside ONE declared VPS restart window (R3 discipline: md5-verified scp + backup, operator confirm before anything broadcasts).

> **Declared window #1: 2026-11-02 08:00 UTC (09:00 WAT).** Operator-confirmed. Scope = **§6.2 only** (deploy with `FPZ_LIVE_ENABLED` unset ⇒ `false`). It does *not* flip the flag and does not require the cohort verdict, the SafetyNet fix, or the `client_oid` fix. The flip is a separate, later window per §9. Executable checklist: [r4-window-1-runbook.md](r4-window-1-runbook.md). Verified pre-window on 2026-11-01: SSH key auth works, `tars-agent`/`tars-scheduler`/`tars-paperz` active, `DRY_RUN=true` explicit in `/etc/tars/agent.env`, no `FPZ_*` keys present, and `tars-paperz` runs from `/opt/tars/paperz` so this deploy cannot touch the cohort.

| Phase | Condition | Action |
|---|---|---|
| 6.1 | Gate verdict PASS (≥30 trades, net > $0, no kill anomalies) | Write spec → code → tests → local green |
| 6.2 | Declared window opens | Deploy to VPS with `fpz_live_enabled=False` (knob present, inert) — **window #1** |
| 6.3 | Same window, operator confirm | Flip `fpz_live_enabled=True`, `DRY_RUN=true`; observe ≥1 cycle — **separate window** |
| 6.4 | ≥5 dry-run SOL cycles clean, no regressions | Flip `DRY_RUN=false` only with explicit `ALLOW_LIVE` + `AGENT_API_TOKEN` |
| 6.5 | Live | Kill switch armed; `/risk-stats` monitored; first loss trips cooldown per gate |

Rollback: set `fpz_live_enabled=False` (single env var, restart). No partial state — the knob is read at agent construction.

---

## 7. Regression tests (to be added)

These guard the three wiring problems. All hermetic — no network, no keys.

### 7.1 `tests/test_fpz_wiring.py`

**test_fpz_disabled_keeps_current_behavior**
- `fpz_live_enabled=False`: SOL signal path identical to pre-wiring (ensemble consulted, arb package priority unchanged).

**test_fpz_bypasses_ensemble_for_sol**
- `fpz_live_enabled=True`, `fpz_bypass_ensemble=True`: SOL ensemble output equals fpz signal direction/confidence exactly; other signals present in `individual` but not in ensemble rationale.

**test_fpz_ensemble_member_when_bypass_off**
- `fpz_bypass_ensemble=False`: ensemble contains fpz as a weighted member; funding_rate can outvote fpz (current behavior preserved).

**test_arb_package_blocked_in_extreme_funding**
- `fpz_arb_package_priority=False`, `abs(funding_rate) >= fpz_arb_block_band_bps`: `_funding_arb_opportunity` returns False for SOL; directional fpz path runs instead.

**test_arb_package_allowed_below_band**
- `fpz_arb_package_priority=False`, `abs(funding_rate) < fpz_arb_block_band_bps`: arb package may run for SOL (if manager capacity allows).

**test_arb_package_priority_preserved_when_flag_true**
- `fpz_arb_package_priority=True`: arb package runs at funding ≥ `funding_arb_min_rate` even when fpz would fire SHORT (current behavior).

**test_fpz_confidence_floor_blocks_weak_signal**
- `fpz_confidence_floor_bps=7000`, fpz signal at 6500 bps: no order, `rejected_by` names confidence floor.

**test_fpz_rejects_missing_price_d3**
- fpz signal with `entry_price=None`: no `OrderRequest` constructed (D3 fail-closed intended_price).

**test_fpz_non_sol_assets_unchanged**
- BTC-USDT-SWAP behavior identical with `fpz_live_enabled=True` (fpz is SOL-only).

**test_risk_gate_still_applies_to_fpz**
- A fpz signal that fails `check_order` (e.g. over position limit) is rejected, not executed.

### 7.2 Where tests live

- `tests/test_fpz_wiring.py` — new file, monkeypatches `AutonomousTradingAgent` with fake `OkxCli`, `RiskGate`, `MultiLegExecutionManager`.
- Import `funding_persistence_z_signal` from `src.signals` for unit-level z/persistence checks already covered by `test_paper_trade_sol.py`.

### 7.3 Test doubles

- Fake `RiskGate.check_order` returning `RiskCheckResult(approved=..., code=..., reason=...)`.
- Fake `MultiLegExecutionManager.can_open(asset)` returning `(True, None)` or `(False, reason)`.
- Fake `OkxCli.run` returning market data with controllable funding rate and prices.

---

## 8. Verification before merge

1. `python -m pytest tests/ -q` — all green (C:\Python314\python.exe).
2. `tests/test_fpz_wiring.py` added and passing.
3. `git diff` — no `.env`, no secrets, no `AGENT_WALLET_PRIVATE_KEY`/`AGENT_API_TOKEN`.
4. Gitleaks clean (`.github/workflows/secret-scan.yml`).
5. Spec matches code: every knob in §4.1 declared in `src/settings.py`; every behavior in §5 traceable to `src/agent.py` `_process_asset` / `_generate_signals` / `_funding_arb_opportunity`.

---

## 9. Open questions (parked, not blocking build)

- Should `fpz_confidence_floor_bps` default to 7000 (signal's own mapping tops at 9200; gate default is 6000 via `min_confidence_bps`)? The 7000 floor matches the signal's "strong cases land ≥7200 bps" design intent.
- `fpz_arb_block_band_bps=150` bps: is 1.5%/8h the right band? It sits between the mild band (±0.0003 = 3 bps) and the arb floor (0.001 = 100 bps). Tune only with data, not vibes.
- After the cohort passes, does `fpz_live_enabled` flip require a *new* declared window, or can it ride the R3 window? Recommendation: separate window — the gate verdict and the wiring deploy are distinct decisions.

---

## 10. Decision record

| Decision | Status | Where recorded |
|---|---|---|
| fpz is SOL-only | Proposed | §4.1 `fpz_sol_only=True` |
| fpz bypasses ensemble by default | Proposed | §4.1 `fpz_bypass_ensemble=True` |
| fpz has priority over arb package by default | Proposed | §4.1 `fpz_arb_package_priority=False` |
| Arb block band = 150 bps | Proposed | §4.1 `fpz_arb_block_band_bps` |
| Confidence floor = 7000 bps | Proposed | §4.1 `fpz_confidence_floor_bps` |

All decisions provisional until the paper cohort verdict lands. This spec is the build contract; nothing in it executes until §6.1's precondition (PASS verdict) is met.

---

## 11. Implementation status — 2026-11-01 (R4 code written, inert)

**Built (knob defaults reproduce pre-wiring behavior; nothing is live):**

| File | Change |
|---|---|
| `src/settings.py` | Six `fpz_*` knobs, declared once, boot-validated (spec §4.1 defaults). |
| `src/agent.py` | `_fpz_scope_applies` / `_fpz_blocks_funding_arb` / `_fpz_decision_signal` / `_audit_fpz_decision`; `_process_asset` now selects a `decision` signal (the fpz signal on the bypass path, else the ensemble) and sends *that* through the unchanged SafetyNet → RiskGate → onchain-log → execute chain; `_funding_arb_opportunity` gains the AC-1 band block. |
| `src/main.py` | Passes the six knobs from settings into the agent. |
| `src/scheduler.py` | Same six knobs in `TradingScheduler.initialize()`. Required, not cosmetic: `scripts/deploy_vps.sh` runs `python -m src.scheduler`, so a flip wired only into `main.py` would leave the deployed live loop on constructor defaults — a silent no-op (review finding). |
| `.env.example` | Documents all six, with the flip-gate and rollback in the comment. |
| `tests/test_fpz_wiring.py` | 18 hermetic regression tests (spec §7.1 plus the seven added below). |

**Interpretation decisions made while implementing (spec was ambiguous):**

1. **`fpz_confidence_floor_bps` applies only on the bypass path.** EB-1 says `fpz_bypass_ensemble=False` "restores current behavior (ensemble decides)". The ensemble divides confidence by the signal count (~2000-3000 bps for SOL), so imposing a 7000 floor there would make the escape hatch permanently untradeable — i.e. `fpz_bypass_ensemble=False` would silently mean "no SOL trades". With bypass off, the gate's own `min_confidence_bps` governs exactly as today.
2. **A NEUTRAL fpz signal is an abstention, not a floor rejection.** `_fpz_decision_signal` returns the fpz signal even when it abstains (z below threshold, flat history); that correctly means NO trade this cycle, and it is recorded as `rejected_by="abstained"` — not routed through the confidence floor, and not left for SafetyNet to report as a non-tradeable-signal block. Regression: `test_fpz_neutral_abstains_without_floor_rejection` (built so the ensemble is non-neutral there, so a silent fpz→ensemble fallback fails it).
3. **Curator eligibility is part of wiring scope.** `_generate_signals` drops strategies the active profile does not allowlist, so a profile whose `enabled_signals` omits `funding_persistence_z` (e.g. `conservative`) leaves the wiring with nothing to select. `_fpz_scope_applies` therefore also requires the strategy to be allowed; otherwise AC-1 would block the delta-neutral package while the signal it yields to does not exist — worse than either behavior on its own. Regression: `test_fpz_not_wired_when_curator_profile_excludes_it`. (`standard`, the repo default, does allowlist fpz.)
   - **Caveat, and a separate pre-existing defect:** `defensive` has `enabled_signals: []`, and `_resolve_curator_profile` maps an empty list to `_enabled_signals = None` ("no filter"), so `defensive` currently allows *every* strategy, fpz included. The scope predicate mirrors that actual behavior, so wiring and pipeline stay consistent — but the profile does not do what its name says. Fixing the `[]` → `None` inversion changes which strategies trade under a profile and is therefore **not** part of R4; it needs its own change and test.
4. **Scope is snapshotted once per asset.** `_process_asset` computes `fpz_scoped` once and threads it through the arb check and the signal selection, so the two fpz arbitration steps inside one asset cannot disagree even if a concurrent `/api/v1/curator-profile` call re-resolves `_enabled_signals`. This does not change the pre-existing pattern that `_generate_signals` reads the shared `_enabled_signals` when it filters; the snapshot covers the R4 decisions only.
5. **The AC-1 band block requires an fpz signal to exist for that asset.** `fpz_sol_only=False` must not extend the block to BTC/ETH, where `_generate_signals` never emits fpz. Regression: `test_sol_only_false_does_not_extend_arb_block_to_other_assets`. Phase 5 extends `_FPZ_SIGNAL_PREFIXES` only after a symbol clears its own gate.
6. **`arb_package_blocked` requires a wired manager.** Without one the package was never attempted, so reporting "blocked" would be false.
7. **The scheduler now inherits `Settings` boot validation.** Reading the knobs through `get_settings()` in `TradingScheduler.initialize()` means the live loop also enforces `Settings`' validators — notably `ALLOW_LIVE=true` requires an explicit `DRY_RUN`. Previously the scheduler never constructed `Settings` and would start anyway. This is fail-closed and intended, but it is a NEW way for the systemd unit to refuse to start: the failure is logged at CRITICAL naming `ALLOW_LIVE`/`DRY_RUN` before the exception propagates. Deployed `.env` files must set `DRY_RUN` explicitly.
8. **Operator-visible counter change on the fpz path only.** A NEUTRAL fpz cycle now emits `fpz_decision` (`rejected_by="abstained"`) instead of `safety_net_block`, because the abstention short-circuits before SafetyNet. No order can be affected (no `OrderRequest` exists on that path), but a dashboard counting `safety_net_block` stops seeing SOL while the flag is on — the fpz decision records are the replacement signal.

**Added regression tests beyond §7.1:** abstention, curator eligibility, `fpz_sol_only=False`, cold-start band behaviour, the approved-record §4.4 field set, scheduler knob wiring, and the settings defaults/env tests.

**Verified:** `python -m pytest tests/ -q` → **1392 passed** (all 18 new tests included); `git diff` and the untracked set carry no secrets (pattern scan; `gitleaks` is not installed on this host).

**NOT verified:** that flipping `fpz_live_enabled=True` produces a live order (see §12), and the onchain-log ordering under `dry_run=False` (no fpz test exercises it; the path is shared with every other order and unchanged by R4). Known edge: an exception raised between the `no_order` check and the approved record (e.g. inside `check_order`/`score_order`) propagates to the cycle's `gather` as an asset error and leaves no `fpz_decision` record for that cycle — pre-existing exception shape, not an audit-path decision.

---

## 12. SafetyNet unit mismatch — fixed 2026-11-10

The original `src/safety_net.py` rejected every directional order, fpz included,
by comparing a per-period funding *rate* sum against a *dollar* hurdle. The
contract is now explicit: carry return is per dollar of position notional, so
SafetyNet multiplies it by the risk-sized notional (`risk_usd / stop_pct`) and
compares the resulting USD expected PnL with the USD hurdle (`risk_usd *
target_rr`).

| Quantity | Value for a 9200-bps fpz SHORT on SOL @ funding 0.004 | Units |
|---|---|---|
| `exp` (`expected_carry_pnl` fallback path) | `0.004*21 - 0.000305*21 ≈ 0.08` | return per $1 notional |
| `exp * (risk_usd / stop_pct)` | `0.08 * (500 / 0.015) ≈ 2,586` | USD expected PnL |
| `risk_usd * target_rr` | `500 * 1.5 = 750` | USD |

The corrected comparison is dimensionally valid and is covered by dedicated
realistic/uneconomic/stop-distance regressions. SafetyNet exceptions now also
produce an explicit rejection and audit record; they cannot remove the size cap
and continue toward the RiskGate.

Consequences:

- **This blocker is closed in code, but it does not authorize live trading.** The paper cohort verdict, SafetyNet operational evidence, `client_oid` fix, and separate declared rollout window remain required.
- **The R4 tests stub `src.safety_net.safety_net`** (as `tests/test_onchain_chaos.py` already does) precisely so the fpz→RiskGate path is exercised. The stub is disclosed in the test module docstring; unstubbed, every "approved" assertion would be unreachable and every "rejected" assertion would pass for the wrong reason.

**Recommended order:** re-run the paper cohort with the corrected SafetyNet → record its verdict → then separately review the remaining live-wiring blockers.

The chosen conversion uses the actual risk-sized notional, including the
signal-derived stop distance. This avoids the earlier accidental correctness
when `stop_pct` happened to equal 1%.

### 12.1 Second go-live prerequisite — `client_oid` exceeds OKX's 32-char `clOrdId`

`_signal_to_order` builds `client_oid = f"signal_{strategy}_{uuid4().hex[:8]}"`. For fpz that is `signal_funding_persistence_z_ab12cd34` = **37 chars**, over OKX's 32-char `clOrdId` limit. `ml_funding_carry` is already 33 (pre-existing). No length guard exists in the executor, so once §12 is fixed the order would be decision-logged, risk-approved, then refused at submission — the same "approved in the audit, rejected by the venue" mismatch the fill-verification path exists to prevent.

Not changed here: `client_oid` is order identity, also used for reconciliation/lookup, so shortening or hashing it is a change to order identity that needs its own test and review. Truncate the strategy segment (or hash it) as a separate, dedicated change before any live flip.

---

## 13. Residual interaction to confirm before flipping the flag

With the spec's defaults, the delta-neutral package still pre-empts fpz in one band. Verified against the wired code (band 150 bps, `FUNDING_ARB_MIN_RATE` 0.001):

| `funding_rate` | band block | package eligible | what actually trades |
|---|---|---|---|
| +0.0200 (200 bps) | yes | — | fpz (if it fires) |
| +0.0150 (150 bps) | yes (boundary is `>=`) | — | fpz (if it fires) |
| +0.014999 (150 bps −1) | no | yes | **package — fpz pre-empted** |
| +0.0015 (15 bps) | no | yes | **package — fpz pre-empted** |
| +0.0010 (10 bps) | no | yes | **package — fpz pre-empted** |
| +0.0005 (5 bps) | no | no (below floor) | fpz (if it fires) |

So fpz owns the cycle at `|funding| >= 150 bps` and below the 10 bps package floor; in `[10, 150)` bps the package wins. That is the reading §5.3 chose ("below the band, the arb package may run") and it is economically defensible — 10-150 bps per 8h is very large carry — but it is a real constraint on how often the validated edge can act. **Before flipping, compare the band to the paper cohort's realized funding distribution**: if the cohort's profitable entries cluster inside `[10, 150)` bps, the default band starves fpz even though every test passes. Tune the band with that data, not by feel.

### 14. Entry protection prerequisite — attached TP/SL

SafetyNet approvals now carry the signal-derived stop and take distances into
absolute OKX trigger prices. Both are attached to the opening order; an entry
declared as requiring protection is rejected by the executor if either trigger
is missing. This change is paper-safe and does not alter `FPZ_LIVE_ENABLED`.

---

