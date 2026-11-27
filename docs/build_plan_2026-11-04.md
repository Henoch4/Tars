# Tarstrade — Canonical Whole-System Build Plan

**Updated:** 2026-11-27  
**Authority:** the current repository and verified runtime evidence.

Tarstrade is a paper-first, multi-asset research and risk-control system.
Live execution is disabled. GTA is historical analytics only and is not an
active promotion target.

## Verified state

- VPS API and scheduler are active; latest scheduler cycles produced no
  executions.
- SOL funding paperz is active, but remains at 0/30 closed trades and $0 net
  carry.
- The main memecoin satellite is active in paper mode: 0 open positions, $0
  currently spent, 135 paper entries and 135 paper exits, approximately
  +$67.72 aggregate realized paper result.
- The memecoin supervisor shadow and GTA shadow are stopped intentionally.
- A dedicated `tars-meme-shadow` VPS unit is not installed.
- Provider-quality tracking and a read-only provider-quality API now exist.
- The current full test collection is **not green** in this checkout: 15
  collection errors were observed. Older test-count claims are not current
  evidence until these errors are repaired and the suite is rerun.

## Product map

| Asset class | Strategy / rail | Chain or venue | State |
|---|---|---|---|
| Crypto majors | Carry / funding | OKX and Binance adapters | Paper research |
| Memecoins | Core satellite and age variant | Solana | Paper / shadow |
| Utility tokens | Safety and sellability research | Solana | Gated |
| Equities | Future research | Planned venue | Planned |
| Audit and vault rails | Decision evidence | X Layer testnet | Infrastructure |
| Perpetuals | Market-data integration | Injective testnet | Integration |

No UI state, configuration, event window, or funded wallet is a live
authorization.

## Correct build order

### Phase 0 — Repository integrity and truthful QA (current P0)

Repair the 15 collection failures before adding features:

- restore or remove references to missing lifecycle modules;
- restore missing smoke and safety-check scripts;
- restore expected paperz constants and ledger exports;
- resolve the missing `KEYPATH_DEFAULT` API;
- make API imports independent of reachable RPC in paper/test mode;
- add CI collection checks and secret scanning.

**Exit:** offline collection succeeds and no test contacts production RPC at
import time.

### Phase 1 — One runtime and lifecycle contract

Use one lifecycle model for every strategy/satellite:
`planned`, `paper`, `shadow`, `halted`, `gated`, `retired`.

Expose owner, effective mode, configuration digest, last progress, and blocker.
API, scheduler, and paper runners must construct the same safety dependencies.

### Phase 2 — Execution ownership and durable state

Implement one execution owner per account/book, durable intent state, idempotent
client IDs, unknown-outcome reconciliation, restart recovery, and safe exits
when entries are halted. Corrupt or unwritable state blocks new entries.

### Phase 3 — Main satellite evidence control plane

Continue emitting provider status and reasons for every screen. Add rolling
metrics for completeness, unknown/error/rate-limit/stale/conflict rates,
rejection mix, entries, exits, and net results. Run a controlled four-hour
paper observation. Separate data-quality rejection from genuine safety rejection.
Do not retune thresholds during the window.

### Phase 4 — Paper/agent parity and FPZ lifecycle

Capture one settled funding sample per venue period. Reconcile paper and agent
decisions on identical inputs. Track quantity-first entry, hold, partial/full
close, funding, fees, realized PnL, remaining exposure, restart recovery, and
verified closed-trade evidence. Remove estimated-profit claims.

### Phase 5 — Operator UI and observability

Show state before controls: asset class, strategy, chain/venue, lifecycle, mode,
owner, last progress, blocker, exposure, and evidence age. Hide or disable
unavailable live actions. Keep provider quality, satellite health, FPZ cohort,
and experiment evidence visibly separate. Complete mobile, keyboard, and error
state QA.

### Phase 6 — Release artifact and deployment verification

Generate dependency/import manifests, run fresh-host paper smoke, verify systemd
templates and persistent paths, rehearse backup/rollback, scan secrets, and
deploy only in a declared maintenance window.

### Phase 7 — Strategy research and expansion

After Phases 0–6 are stable, evaluate the SOL cohort, memecoin age variant,
utility-token safety research, equity/tokenized-stock research, and new venues
as isolated paper experiments. Every strategy gets its own evidence record,
lifecycle, risk budget, and promotion decision.

### Phase 8 — Live authorization (future and separately governed)

Requires complete fresh safety evidence, execution ownership, reconciliation,
clean paper/shadow results, a smallest approved round trip, explicit human
authorization, a declared window, hard budgets, and emergency flattening.
Calendar dates and funded wallets never authorize live execution.

## Non-goals

- No GTA restart or GTA live promotion.
- No automatic funding, signing, or broadcasting.
- No strategy tuning from one short window.
- No frontend-framework migration before runtime and QA are stable.
- No public raw blotters, wallet-operation scripts, provider snapshots, or
  VPS-specific secrets.

## Whole-system dependency map and operator task tray

The task tray is the operator-facing projection of verified system state. It is
not a second source of truth and it must never create an order. Each item must
carry `owner`, `lifecycle`, `mode`, `blocker`, `evidence_age`, `next_action`,
and `required_authorization`.

Dependency order:

1. **Integrity:** collection/import health, secret scan, typed configuration.
2. **Ownership:** durable intent ledger, fencing, idempotency, unknown-outcome
   reconciliation.
3. **Risk:** quantity-first multi-leg recovery, SafetyNet, daily-loss ledgers,
   allowlists, slippage/fill verification.
4. **Evidence:** provider freshness/completeness, enrichment, experiment
   snapshots, rejection and outcome matching.
5. **Lifecycle:** paper/shadow parity, restart recovery, emergency flatten,
   milestone verdicts.
6. **Operations:** health/readiness probes, capability manifest, deployment
   artifact closure, rollback and backup rehearsal.
7. **Promotion:** explicit operator approval only after all prior dependencies
   are green; calendar dates and funded wallets never satisfy this gate.

Suggested task-tray record:

```json
{
  "task_id": "stable-id",
  "system": "paperz|meme|gta|platform",
  "lifecycle": "planned|paper|shadow|halted|gated|retired",
  "mode": "dry_run|observation|live_disabled|live_authorized",
  "owner": "service-or-human",
  "status": "blocked|ready|running|needs_review|complete",
  "blocker": "explicit reason or null",
  "evidence_age_s": 0,
  "next_action": "single safe action",
  "required_authorization": "none|operator|separate_live_approval",
  "updated_at": "timestamp"
}
```

The tray must distinguish “implemented”, “wired”, “paper-tested”, “deployed”,
and “live-authorized”; a green unit test or healthy HTTP endpoint cannot imply
the latter states.

## Verification commands

```powershell
python -m pytest tests/ -q
python -m compileall src scripts
git diff --check
```

A green suite proves software behavior only; it does not prove profitability or
authorize trading.
