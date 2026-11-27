# Audit-Ledger Reconciliation — On-Chain vs Off-Chain Daily Loss

**Purpose:** Document the differences between the two daily-loss ledgers that
exist in Tarstrade, specify how they are calculated, what happens when they
disagree, and why neither is authoritative alone until reconciliation is
demonstrated.

**Status:** Read-only design documentation. No state changes, no private keys.

---

## 1. The two ledgers

| Attribute | On-chain ledger | Off-chain ledger |
|-----------|----------------|------------------|
| **Contract / module** | `TradeAuditTrail.sol` | `src/execution/risk_gate.py` (`RiskGate`) |
| **Data source** | `recordExecution` execution receipts | `report_loss` from `agent.py:_report_cycle_loss` |
| **Loss calculation** | Direction-aware P&L per fill (long: fill < entry = loss; short: fill > entry = loss), ceiling division rounding up | Equity delta between consecutive OKX total-equity readings; negative delta reported as loss |
| **What counts as loss** | Successful fills only (`success == true`) that move against the position | Any cycle-to-cycle equity decrease reported via `report_loss` |
| **Accumulator** | `dailyLoss[agent]` (`uint256`, 1e8 fixed point) | `_daily_loss[agent_id:utc_day]` (float, USD) |
| **Also tracks** | `dailyVolume`, `dailyTrades` (hard-capped at 100 trades/day in `logDecision`) | `_daily_volume`, `_daily_trade_count` |
| **Keying** | Wallet address (Ethereum `address`) | `agent_id:utc_day` string (e.g. `"default:2026-10-27"`) |
| **Time boundary** | UTC day rollover at `block.timestamp / 86400` (block-time based) | UTC day key from `datetime.now(timezone.utc).strftime("%Y-%m-%d")` (wall-clock based) |
| **Day reset** | Automatic in `logDecision` when `today > dailyBucket[agent]` | Automatic in `_day_key` at midnight UTC |
| **Persistence** | On-chain storage (permanent, immutable) | Persisted to durable counters JSON file (`DurableDailyCounters`) so restarts recover mid-day counters |
| **Read function** | `getAgentDailyStats(agent)` → `(volume, loss, trades)` | `get_daily_stats(agent_id)` / `current_day_key(agent_id)` |
| **Write function** | `recordExecution` (state-changing, signer-gated) | `report_loss(agent_id, loss_usd, day_key)` (in-process, no chain write) |

---

## 2. Calculation methods

### 2.1 On-chain loss (`recordExecution`, lines 306–363)

```
if success:
    if !isShort && fillPrice < entryPrice:
        lossUsd = ceil((entryPrice - fillPrice) * sizeUsd / 1e8)
    elif isShort && fillPrice > entryPrice:
        lossUsd = ceil((fillPrice - entryPrice) * sizeUsd / 1e8)
    if lossUsd > 0:
        dailyLoss[msg.sender] += lossUsd
```

- **Direction-aware:** A price drop below entry is a loss for a long but a gain for a short (and vice versa). This corrects an earlier bug that treated any fill below entry as a loss regardless of direction.
- **Ceiling division:** Rounding up means losses are slightly overstated (conservative).
- **Only successful fills:** `success == false` fills contribute zero loss.
- **No loss on unrealized P&L:** Unrealized moves without an execution receipt do not change `dailyLoss`.
- **No gain netting:** Winning fills do not reduce `dailyLoss`. Losses accumulate monotonically within the day.

### 2.2 Off-chain loss (`agent.py:_report_cycle_loss` + `RiskGate.report_loss`)

```
delta = equity_now - equity_prev_cycle
if delta < 0:
    risk_gate.report_loss(agent_id, -delta, day_key=cycle_day_key)
```

```
def report_loss(self, agent_id, loss_usd, day_key=None):
    key = day_key or self._day_key(agent_id)
    loss = self._daily_loss.get(key, 0.0) + loss_usd
    self._daily_loss[key] = loss
    if loss >= self.max_daily_loss_usd and not self._kill_switch_active:
        self.activate_kill_switch(...)
```

- **Equity-delta model:** Compares OKX `total_eq` (total equity including unrealized P&L) between consecutive live cycles.
- **Cycle-attributed:** Loss is pinned to the UTC day the *cycle started in* (via `current_day_key()`), not the day the cycle ended. This prevents a cycle straddling midnight from booking post-midnight losses to the wrong day.
- **Disabled in dry-run:** No real account to read, so no loss is reported during paper trading.
- **Failed read is non-fatal:** If the OKX balance read fails, the cycle continues and no loss is reported — the loss is simply missed for that cycle.
- **Kill switch auto-trip:** If accumulated daily loss reaches `max_daily_loss_usd`, the gate trips its kill switch (in-process halt, separate from the on-chain kill switch).

---

## 3. Time boundaries and day rollover

| Aspect | On-chain | Off-chain |
|--------|----------|-----------|
| **Day definition** | `block.timestamp / 86400` (integer division) | `datetime.now(timezone.utc).strftime("%Y-%m-%d")` |
| **Rollover trigger** | First `logDecision` call after the block timestamp crosses a UTC day boundary | First `report_loss` or `score_order` call after midnight UTC |
| **Drift risk** | Block-time based; if blocks are irregular, the boundary shifts with block time, not wall-clock UTC | Wall-clock based; depends on the VPS/system clock being correct |
| **Midnight straddle** | A decision logged at 23:59:59 and one at 00:00:01 are in different buckets only if a block crossed between them | Same — pinned to cycle start day via `day_key` param |

**Implication:** The two ledgers can attribute the same loss to different days if a cycle straddles midnight and block timing differs from wall-clock timing. This is not a bug in either system — it's a different definition of "day" — but it means the two ledgers will not always match day-for-day.

---

## 4. What happens when they disagree

The two ledgers **can and will disagree** for structural reasons, not bugs:

### 4.1 Sources of disagreement

| Source | Effect |
|--------|--------|
| **Different keys** | On-chain keys by wallet address; off-chain by `agent_id:utc_day`. A single-agent deployment maps 1:1 today, but the mapping is not defined in code — seeding one from the other would require an explicit address↔agent_id mapping. |
| **Different loss models** | On-chain counts per-fill P&L from execution receipts; off-chain counts cycle-to-cycle equity deltas (which include unrealized P&L, fees, funding, and any non-trade equity movements). |
| **Different timing** | On-chain records at execution time; off-chain reports at cycle end. A fill executed at 14:59 but reported at 15:01 is attributed to the cycle's start day off-chain, but the block's day on-chain. |
| **Different fill handling** | On-chain ignores `success == false` fills; off-chain only sees what the OKX balance reflects after settlement. |
| **Missing data** | A failed OKX balance read means zero off-chain loss reported for that cycle (loss is missed). A failed chain read means `sync_with_onchain` logs a warning and proceeds with local state (non-blocking). |
| **Rounding** | On-chain uses 1e8 fixed-point ceiling division; off-chain uses float USD. Small discrepancies (~$0.01) are expected. |

### 4.2 Disagreement handling (per `src/execution/risk_gate.py:sync_with_onchain`)

The existing cross-check (`sync_with_onchain`, lines 511–553) defines the following behavior:

| Condition | Action |
|-----------|--------|
| On-chain kill switch active, local gate inactive | **Trip local gate.** On-chain halt is non-overridable authority; a stale/cleared local gate must never under-report an onchain halt. |
| Chain read fails | Log warning, proceed with last known-good local state (file-backed counters). Reconciliation is a safety cross-check, not a blocking read. |
| Local counters vs on-chain accumulators | **Not synced.** The contract's accumulators are NOT used to seed the local accumulators (key mapping undefined). The contract's accumulators are used *only* for the kill-switch sync above. |

### 4.3 Neither ledger is authoritative alone

| Dimension | On-chain is authoritative for | Off-chain is authoritative for |
|-----------|------------------------------|-------------------------------|
| Halt decision | Kill switch state (non-overridable) | In-process kill switch state |
| Execution-side P&L record | `dailyLoss` from `recordExecution` receipts | — |
| In-process running totals | — | `_daily_loss` (between restarts) |
| Restart recovery | — | Durable counters JSON file |
| Daily limit enforcement | Contract rejects `logDecision` when `dailyLoss >= maxDailyLossUsd` | Gate trips kill switch when `loss >= max_daily_loss_usd` |

**Rule:** Until reconciliation is demonstrated (i.e., the two ledgers are shown to track each other within an agreed tolerance over a sustained period), **neither ledger is the single authoritative measure of daily risk.** A breach on one ledger does not automatically imply a breach on the other.

---

## 5. Missing-data handling

| Failure mode | On-chain | Off-chain |
|--------------|----------|-----------|
| RPC/chain unreachable | `sync_with_onchain` catches exception, logs warning, proceeds with local state | N/A (no chain dependency) |
| OKX API unreachable | N/A | `_report_cycle_loss` catches exception, logs warning, returns without reporting loss |
| Missing decision owner | `recordExecution` reverts `"not decision owner"` | N/A |
| Missing decision (execution before log) | `recordExecution` reverts `"decision not logged"` | N/A |
| Corrupt counter file | N/A | `DurableDailyCounters` snapshot returns defaults; gate proceeds with zeroed counters for that key |

---

## 6. Recommended reconciliation procedure (pre-G1, read-only)

Until the release gate passes and `setRiskParams` is configured, this procedure can be run read-only to characterize the divergence:

1. **Snapshot both ledgers at the same wall-clock time:**
   - On-chain: `getAgentDailyStats(agent)` → `(volume, loss, trades)`
   - Off-chain: `RiskGate.get_daily_stats(agent_id)` for the current UTC day

2. **Normalize units:**
   - On-chain `loss` is in 1e8 fixed-point units → divide by 1e8 for USD
   - Off-chain `loss` is already in float USD

3. **Compute discrepancy:**
   - `discrepancy_usd = onchain_loss_usd - offchain_loss_usd`
   - `discrepancy_pct = discrepancy_usd / max(onchain_loss_usd, 0.01) * 100` (avoid divide-by-zero)

4. **Classify the discrepancy:**
   - `|discrepancy_pct| < 1%` → rounding/model noise, acceptable
   - `1% <= |discrepancy_pct| < 10%` → investigate (cycle straddle, missing fill, failed balance read)
   - `|discrepancy_pct| >= 10%` → potential data integrity issue; do not treat either ledger as authoritative

5. **Log and alert:** Record discrepancy with timestamp, both ledger values, and classification. Do not auto-halt on discrepancy alone — the discrepancy is expected behavior until the mapping and models are aligned.

---

## 7. What must be true before either ledger is treated as authoritative

| Criterion | Status |
|-----------|--------|
| `setRiskParams` called with non-zero `maxPositionSizeUsd` and `maxDailyLossUsd` | ❌ Not yet (zero on testnet) |
| On-chain vs off-chain loss tracked over ≥1 sustained period | ❌ Not yet (paper trading, no on-chain decisions) |
| Address↔agent_id mapping defined | ❌ Not yet (single-agent deployment, mapping implicit) |
| Discrepancy tolerance agreed and documented | ❌ Not yet |
| Kill switch verified operational (can be activated/deactivated) | ❌ Not yet (inactive on testnet) |

Until these are satisfied, the on-chain `dailyLoss` and the off-chain `_daily_loss` are **informational only** — neither gates live capital, and neither should be cited as proof of daily risk position.

---

**Status:** Document complete. Ready for deliverable 3 — post-G1 `setRiskParams` configuration procedure — once you're ready. That procedure will remain non-executable until G1 passes and the release gate is approved.
