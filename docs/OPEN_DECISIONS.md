# Open Design Decisions — TARS (Trade Audit & Risk System)

**Status:** Open — decisions outsourced to an independent reviewer.
**Repo:** Tarstrade root (`C:\Users\Henoch\Documents\Programming Folder\Tarstrade`)
**Date written:** 2026-08-28
**Context for the decider:** The system is a crypto trading agent whose core
claim is "written first, traded second" — every trade decision is logged
onchain (X Layer / T3N enclave) *before* execution, gated by a risk layer the
strategy cannot override. Current state: **420 tests pass, demo/testnet
ready, not live-capital ready.** A full functionality review (Aug 2026) fixed
ten issues and left the items below, each of which needs a product/policy
decision rather than a mechanical patch. Naive fixes for several of them make
the system *worse*, which is why they are being outsourced instead of just
coded.

Read each section independently. Each has: background, why it wasn't just
fixed, options, and the specific decision requested. Constraints that apply
to ALL decisions are at the bottom.

---

## Decision 1 — Multi-leg partial-fill accounting (BLOCKER for live multi-leg)

**Background.** The multi-leg manager (`src/multi_leg.py`) submits hedged
packages (e.g. long spot + short perp) serially. A leg whose exchange state is
`PARTIALLY_FILLED` is currently treated as fully filled (`filled = state in
(FILLED, PARTIALLY_FILLED)`, multi_leg.py ~:364+). So a 10%-filled hedge leg
lets the package LOCK as if fully hedged, leaving 90% of one leg naked with
no alert.

**Why it wasn't patched.** The obvious fix — treat partial as not-filled —
makes things WORSE: the resolver (`resolve_partial_fill`) then unwinds the
*other* legs at their **full** notional while only 10% of this leg exists,
over-closing the hedge and leaving the reverse exposure. Correct handling
requires **amount-aware unwinds**: "unwind exactly the filled amount of the
broken leg, and exactly the matching amount of the other legs" — a redesign
of `resolve_partial_fill`/`resolve_slippage_breach` and the `Step.inverse()`
unwind path.

**Options.**
- (a) Full fix: thread per-leg fill ratios through `LegResult`, make unwinds
  fill-ratio-scaled, treat <99.9% fill as breach, add regression tests.
  ~1–2 days of careful work + test impact.
- (b) Conservative gate: reject any live multi-leg package outright when any
  leg lands `PARTIALLY_FILLED` (abort + full unwind of others at their
  *actual filled amounts*, not notional) until (a) exists.
- (c) Document the limitation loudly and keep market-order legs only
  (fill_prob ~1), accepting the risk for paper/testnet only.

**Decision requested:** pick (a), (b), or (c); if (a), approve the
amount-aware unwind spec implied above as the contract for implementation.

---

## Decision 2 — Kill-switch durability across restarts

**Background.** The risk gate's kill switch (`src/execution/risk_gate.py`,
`_kill_switch_active`) is in-memory only. A process restart clears an
auto-tripped switch (daily-loss breach, hard-collar fill breach). The daily
*counters* ARE durable when `counters_durable=True` (atomic write-then-rename
JSON store, `DurableDailyCounters`), so after a restart the gate re-trips on
the first `report_loss`/check — but between restart and next loss, one order
can pass that the pre-crash state would have blocked. The onchain
`TradeAuditTrail.sol` kill switch is durable but requires wiring
`onchain_logger` + a live contract (optional, defaults None).

**Options.**
- (a) Persist switch state into the same `DurableDailyCounters` JSON file
  (new `kill_switch` key: `{active, reason, activated_at}`). ~half day,
  touches gate init that several tests observe.
- (b) Require onchain sync at boot when configured (`sync_with_onchain`
  already exists) and make it MANDATORY for live mode (refuse to trade if
  the reconciliation can't run). More moving parts, strongest guarantee.
- (c) Accept in-memory switch; rely on the fact that counters re-trip it on
  the next loss. Weakest, but zero code.

**Decision requested:** which durability model is the product standard?
(Recommendation from the review: (a) now, (b) before real capital.)

---

## Decision 3 — Fat-finger check for MARKET orders

**Background.** The ±20% fat-finger rejection (`risk_gate.py`, check 6) only
runs on **limit** orders, because it compares the order's limit price to the
market reference. Live default execution is **market** orders, so today the
pre-trade fat-finger gate is inert on the main path; only the *post-fill*
slippage verification compensates (trips kill switch past 2× collar — i.e.,
after the bad fill already happened).

**Options.**
- (a) Pre-trade reference guard for market orders: reject if no fresh
  reference price (already done via check 9) AND reject if the agent's
  *intended* price (mid/last from signal generation) deviates >X% from the
  reference price — catches "the agent computed on garbage data" cases.
  Needs an `intended_price` field on `OrderRequest`.
- (b) Post-trade only (current state), accept that a market order prints
  first and the kill switch reacts after.
- (c) Hybrid: allow market orders only when the ensemble's entry_price exists
  and deviation is within the collar; otherwise require limit orders.

**Decision requested:** (a), (b), or (c) — and if (a)/(c), the deviation
threshold (candidate: reuse `max_slippage_pct`).

---

## Decision 4 — Volume limit: enforce or delete the claim

**Background.** `max_daily_volume` exists and is tracked
(`report_volume`, `_daily_volume`) but the code itself admits it is "NOT an
enforced control" (display-only). READMEs imply limits are enforced.

**Options.**
- (a) Enforce it as check 10b: reject when `(day volume + order notional) >
  max_daily_volume`, persisting via the durable counters like trade count.
- (b) Remove the knob and the doc claim; keep volume as telemetry only.

**Decision requested:** (a) or (b). If (a): where does volume sit relative to
the trade-count quota (independent cap vs. additional gate)?

---

## Decision 5 — Allowlist semantics: exact match or base-asset family?

**Background.** `_base_allowed` (`risk_gate.py` ~:475–488) admits ANY
instrument sharing the base asset: allowlisting `BTC-USDT-SWAP` also admits
`BTC-USD-SWAP`, `BTC-USDC-SWAP`, and dated BTC futures (`BTC-USD-250926`).
The docstring says the widening exists for the funding-arb spot leg
(BTC-USDT when BTC-USDT-SWAP is allowed) and claims "the spot leg… no more" —
the code is broader than the claim. This is a policy question, not a bug: it
defines what the agent can trade without any operator action.

**Options.**
- (a) Exact match + explicit companion list: the funding-arb package declares
  its spot leg explicitly; the gate matches only listed instruments.
  Tightest; requires declaring companions in config.
- (b) Keep base-family matching but restrict to swap/spot siblings on the
  SAME quote ccy (admits BTC-USDT spot; rejects BTC-USD-anything and dated
  futures).
- (c) Keep current behavior; fix the docstring to match reality.

**Decision requested:** which semantics are intended?

---

## Decision 6 — audit_logger hardening (float packing + nonce + scan cost)

**Background.** Three related items in `src/audit_logger.py`:
1. Fixed-point conversion uses `int(value * 1e8)` — binary-float
   representation error (e.g. `int(0.29 * 1e8)` ≈ 28,999,999.99…). Signed
   hash and submitted tx are self-consistent today, but an independent
   verifier recomputing from decimal strings can hit a forensic mismatch.
2. Local nonce counter can diverge from the chain nonce across
   failover/reorg paths (reseeding exists on broadcast failure but is not
   systematic).
3. `get_decision` / `get_contract_stats` scan events from block 0 (O(chain
   length) per query on a mature chain) and hardcode 5760 blocks/day
   (assumes 15s blocks; X Layer's actual block time unverified).

**Options.**
- (1a) Convert via `Decimal(str(value)).scaleb(8).to_integral_value()` —
  exact, unchanged behavior for already-integral values, low test risk.
- (2a) Re-read `w3.eth.get_transaction_count` on failover/reorg detection and
  log a WARN on divergence (extends the existing reseed path).
- (3a) Measure block time once at construction (latest block timestamp
  delta), use it for day-window math instead of the hardcoded 5760.

**Decision requested:** approve all three as one hardening batch, or pick a
subset.

---

## Decision 7 — Curator auto-revert behavior

**Background.** `src/curator.py`: `record_trade_pnl` measures **cumulative**
PnL since the last profile switch, not a trailing window (despite the
`lookback_trades` name) — one early good trade can offset later losses
indefinitely. Auto-revert restores `previous_profile`, which can be the very
profile that caused the drawdown that forced `defensive` — a ping-pong risk.
`_apply_switch`'s `forced` parameter is accepted and ignored.

**Options.**
- (a) Trailing-window PnL for the revert trigger + never auto-revert OUT of
  forced-defensive (requires manual operator action).
- (b) Keep cumulative PnL but add a trailing-window check as a second
  trigger.
- (c) Accept as-is (the curator is an advisory governance heuristic, not a
  safety control — the risk gate is the safety layer).

**Decision requested:** which behavior is intended?

---

## Decision 8 — vault_api latency hardening

**Background.** `src/vault_api.py` `audit_recent` can make up to 100
**sequential** RPC calls per HTTP request on sync `def` endpoints (2s probe
timeout each) — under load this can pin a worker slot for minutes.

**Options.** (a) small TTL cache for contract reads, (b) async + gather
batching, (c) cap `count` at ~20 + paginate, (d) leave as-is for the
single-operator API and document the limit.

**Decision requested:** harden now or document?

---

## Decision 9 — Hygiene batch before the repo goes public (T3N submission)

Low-risk, no semantics. Approve as a batch?
1. Add `requirements-ml.txt` (`lightgbm`, `pandas`, `scikit-learn`) — the ML
   path (`ml/pipeline.py`, `scripts/train_carry_model.py`) needs them but
   no requirements file lists them.
2. Fix stale "343 tests" counts in `twitter_thread.md` and
   `docs/audit-trail-trader-diagram.mmd`; resolve the .mmd's claim that
   SVG/PNG/excalidraw renderings exist (generate them or delete the claim).
3. Add a one-line closure note to `ML_ROADMAP_REVISED.md` for the failed
   carry experiment (report of 08-26); stamp `TRADING_MODEL_ROADMAP.md` as
   superseded at the top.
4. Reconcile `TODO.md`'s "dataset rebuild not re-run" note with the rebuilt
   `data/dataset.csv` (update the note or remove the stale dataset).
5. Remove dead weight: empty `-p/` directory,
   `docs/design/waitlist-confirmation-email.html` (feature withdrawn), the
   `NotImplementedError` placeholder `walk_forward_windows` in
   `scripts/train_carry_model.py`.
6. Decide dead-code policy: leave zero-address fallbacks (`agent.py`),
   `fill_timeout_cycles`, `get_nonce()` (some used by scripts/tests) — or
   strip them.

---

## Global constraints for whoever implements these decisions

1. **Tests are a locked zone.** 420 tests pass; the implementer must not
   edit tests to reach green. If a test pins behavior a decision wants to
   change, that conflict goes back to the human — test changes are a
   deliberate human act, never a side effect of implementation.
2. **Fail-closed bias.** New checks reject on missing/malformed input rather
   than silently passing (house style: `INVALID_ORDER_SIZE`,
   `NO_PRICE_REFERENCE`, `INVALID_LEVERAGE`).
3. **Dry-run stays zero-side-effect.** Nothing added may burn quotas, trip
   switches, or touch OKX in dry-run (see the `count_trade=not self.dry_run`
   pattern).
4. **Honesty invariant.** Docstrings/READMEs never claim a control that
   isn't wired; partial enforcement is documented as partial.
5. **Regression rule.** Every fixed bug gains a permanent test; the test
   count only goes up.
6. **Scope guard.** `t3n/` is a standalone subsystem (separate build, zero
   references from Python CI/tests). None of these decisions affect it; its
   own fixes (host clock, leverage cap, unique decision IDs) are already
   done and verified on the `wasm32-wasip2` target.