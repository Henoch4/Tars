# Roadmap Index — Tarstrade Canonical Plans

**Updated:** 2026-11-27  
**Status:** The canonical execution authority is `build_plan_2026-11-04.md`,
validated against current code and runtime evidence. The older product roadmaps
below remain strategic context; their dates, test counts, and phase claims are
not current status.

---

## Read first

| Document | Purpose |
|---|---|
| [`build_plan_2026-11-04.md`](build_plan_2026-11-04.md) | Current whole-system build order, verified state, gates, and non-goals |
| [`repository_review_2026-11-04.md`](repository_review_2026-11-04.md) | Evidence-backed architecture and QA findings |
| [`inspiration_review_2026-11-04.md`](inspiration_review_2026-11-04.md) | Consolidated future propositions and stealables, with priority boundaries |

## Strategic context (subordinate; not current status)

| # | Document | Lines | Purpose | Last Updated |
|---|----------|-------|---------|--------------|
| **1** | [`mainnet-roadmap.md`](mainnet-roadmap.md) | 262 | **Primary production roadmap** — 9 phases from hackathon → mainnet → audit → legal → real depositors → mobile. Includes fix vs. add inventory, budget, non-negotiable gates. | 2026-08-22 |
| **2** | [`EXPANSION_ROADMAP.md`](EXPANSION_ROADMAP.md) | 323 | **Asset expansion strategy** — Two-books architecture (Book 1: depositor carry vault; Book 2: personal satellite). Sequencing: majors → alts → memecoin (personal) → tokenized stocks. Future venue propositions (Robinhood Chain, Hyperliquid). | 2026-09-08 |
| **3** | [`ML_ROADMAP_REVISED.md`](ML_ROADMAP_REVISED.md) | 341 | **ML model roadmap** — Revised after Phase 2 gate falsified directional edge. New targets: 1) Funding-path model (quantile + hazard), 2) Vol model (EWMA→GARCH), 3) Execution model. Deprioritized: directional classification, LSTM, HMM. | 2026-08-24 |
| **4** | [`PATH_FORWARD.md`](PATH_FORWARD.md) | 150 | **Immediate next steps from first validated edge** — SOL `funding_persistence_z` (Calmar 16.1, 28 trades). Steps: paper trade SOL only (30 trades), don't broaden, harden clockwork, carry bookkeeping → product, land repo-scan gap fillers. | 2026-09-08 |

---

## Supporting Documents

| Document | Purpose |
|----------|---------|
| `PENDING_TASKS_REPORT.md` | Historical reference only; file is not present in this checkout |
| [`OPEN_DECISIONS.md`](OPEN_DECISIONS.md) | 9 open design decisions needing human review (multi-leg partial fills, kill-switch durability, fat-finger, volume limit, allowlist, audit logger, curator auto-revert, vault_api latency, hygiene batch) |
| `TRADING_MODEL_ROADMAP.md` | Superseded; file is not present in this checkout |
| `ML_ROADMAP_ZERO_COST_STRATEGY.md` | Superseded; file is not present in this checkout |
| `VALIDATION_GATE.md` | Superseded/missing; use `src/validation.py` and current tests |

---

## Gap Analysis & Implementation Docs

| Document | Purpose |
|----------|---------|
| [`GAP_ANALYSIS.md`](GAP_ANALYSIS.md) | Tarstrade vs Quant Vault: 5,234 strategy gap, 14 infrastructure layers Tarstrade wins |
| [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) | 5-phase, 8-week plan for 20 Vault strategies → Tarstrade |
| [`STRATEGY_PORTING_REPORT.md`](STRATEGY_PORTING_REPORT.md) | 20 specific strategies with Vault sources, params, integration points, validation targets |

---

## Decision Log (From `OPEN_DECISIONS.md`)

| Decision | Chosen Option | Status |
|----------|---------------|--------|
| 1. Multi-leg partial-fill | (a) Full fix: amount-aware unwinds | **TODO** — Phase 1 blocker |
| 2. Kill-switch durability | (a) Persist to `DurableDailyCounters` | **TODO** — Phase 1 |
| 3. Fat-finger for market orders | (a) Pre-trade reference guard (`intended_price`) | **TODO** |
| 4. Volume limit | (a) Enforce as check 10b | **TODO** |
| 5. Allowlist semantics | (a) Exact match + explicit companions | **TODO** |
| 6. Audit logger hardening | (1a, 2a, 3a) All three | **TODO** |
| 7. Curator auto-revert | (a) Trailing window + no auto-revert out of forced-defensive | **DONE** in `curator.py` |
| 8. vault_api latency | (a) TTL cache + (c) cap count | **TODO** |
| 9. Hygiene batch | Approve as batch | **TODO** |

---

## Phase Gates (From `mainnet-roadmap.md`)

| Phase | Gate | Must Pass Before Next |
|-------|------|----------------------|
| **0** | Critical Blockers | VPS provisioned, Decision 1 fixed, repo synced, keys rotated |
| **1** | Harden Clockwork | 7 items: kill-switch durability, dual ledger reconciliation, fill verification, equity attest, audit logger hardening, volume enforcement, allowlist semantics |
| **2** | Paper-Trade Validated Edge | SOL `funding_persistence_z` only — ≥30 trades, positive net carry, 0 kill-switch trips |
| **3** | Strategy Expansion | 20 strategies each passing validation gate (`cleared_for_paper_trading=true`) |
| **4** | Professional Audit + Bug Bounty | $15-30K audit complete, bounty launched |
| **5** | Legal/Compliance | Legal memo, fee structure, geoblocking |
| **6** | First-Loss Capital + Trust Infra | Operator first-loss tranche on-chain, price-pinged reconciliation |
| **7** | Deposit Sizing + Liquidity | Redeploy vault with new MIN/MAX TVL, fiat on-ramp |
| **8** | Mobile Distribution | Vertically-integrated mobile (Google Pay, Google/X sign-in, no seed) |
| **9** | Growth | Multi-chain inbound (Base, Solana), multi-tranche vaults, tokenized shares |

---

## Validation Gate (Universal)

**Every strategy must pass before curator allowlist admission:**

```bash
python scripts/check_validation_gate.py
# Must output: cleared_for_paper_trading=true
```

```python
# Universal criteria (from src/validation.py)
validation_report(
    returns=in_sample_returns,
    oos_returns=out_of_sample_returns,
    param_grid_returns=grid_returns,
    param_grid_oos_returns=grid_oos,
    calmar_bar=1.0
)
# REQUIRED: cleared_for_paper_trading == true
# REQUIRED: has_oos_evidence == true (no vacuous pass)
# REQUIRED: pbo_analysis.pbo_pass == true (PBO ≤ 0.5)
```

---

## Current Status

See `build_plan_2026-11-04.md` for verified status. In particular, live
execution is disabled, GTA is historical analytics only, and the full test
collection must be repaired before new feature work.

| Area | Status |
|------|--------|
| **Keys** | Rotated 2026-09-12 (old `0x4E80…` invalidated) |
| **VPS** | ✅ Provisioned 2026-10-01 (Contabo Cloud VPS 4, `80.190.82.127`, 641 tests green) |
| **Tests** | Do not rely on historical counts; run the current collection and repair collection errors first |
| **Contracts** | `TradeAuditTrail.sol`, `TradingVault.sol`, `TreasuryMultisig.sol` deployed on X Layer testnet (1952) |
| **Validated Edge** | SOL `funding_persistence_z` — OOS Calmar 16.1, 28 trades, 54% win |
| **Falsified** | Mean reversion, momentum, directional ML, two-head carry model |
| **Phase** | **Phase 0** — repository integrity and truthful QA |

---

## Quick Commands

```bash
# Full test suite
python -m pytest tests/ -q

# Validation gate (run before ANY strategy enters curator)
python scripts/check_validation_gate.py

# Pre-flight check (run before every deploy)
python scripts/preflight_check.py

# Vercel build check (dry-run)
vercel build

# Secret scan
gitleaks detect --source .
```

---

## Navigation Rule

> **When in doubt, read `mainnet-roadmap.md` first.** It is the master document. All other roadmaps (`EXPANSION_ROADMAP.md`, `ML_ROADMAP_REVISED.md`, `PATH_FORWARD.md`) are subordinate and reference its phase gates. `PENDING_TASKS_REPORT.md` tracks the current week's blockers. `OPEN_DECISIONS.md` tracks decisions waiting on human review.
