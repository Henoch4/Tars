# TradeAuditTrail — Risk Guard Semantics

**Contract:** `TradeAuditTrail.sol` (X Layer testnet, chainId 1952)
**Deployed address:** `0x6019b96e9d0Ba17588eb22579d9c2dEf0473d07c`
**Status:** Deployed, pristine state (0 decisions, 0 executions, no risk parameters configured, kill switch inactive)
**Audit basis:** Source review of `contracts/contracts/TradeAuditTrail.sol` + live read-only verification against `https://testrpc.xlayer.tech/terigon`

This document separates **verified code behavior** from **assumptions** and **outstanding tests**. It is read-only engineering reference — no state changes, no private keys, no state-changing interactions described here require a key.

---

## 1. Initialization semantics

### 1.1 Zero-valued parameters

The contract stores `RiskParams` per agent:

| Field | Solidity type | Zero-value meaning |
|-------|--------------|-------------------|
| `maxPositionSizeUsd` | `uint256` | 0 = unset |
| `maxDailyLossUsd` | `uint256` | 0 = unset |
| `maxLeverageBps` | `uint256` | 0 = unset |
| `minConfidenceBps` | `uint256` | 0 = unset |
| `isMarketOrder` | `bool` | false |

All mappings default to zero-valued structs. The testnet contract was verified at this pristine state: every agent's `agentRiskParams` is all zeros.

### 1.2 `setRiskParams` behavior (lines 123–145)

- **Visibility:** `external`, guarded by `onlyAgent` (`msg.sender == tx.origin`; relayer calls rejected).
- **Initial set:** Any non-zero values accepted on first call.
- **Subsequent calls:** Tightening-only — new position cap ≤ old, new daily loss ≤ old, new leverage ≤ old, new min confidence ≥ old. Any attempt to loosen reverts (`"pos too high"`, `"loss too high"`, `"lev too high"`, `"conf too low"`).
- **Side effect:** `isMarketOrder` is always reset to `false` on every call.
- **Event:** Emits `RiskParamsUpdated`.

### 1.3 What unset parameters do to `logDecision` — critical finding

Tracing `logDecision` (lines 191–301) with `maxPositionSizeUsd = 0`:

| Check | Line | Condition with unset params | Result |
|-------|------|---------------------------|--------|
| Kill switch | 196 | `killSwitchActive[msg.sender]` is false | Passes |
| Duplicate decisionId | 199 | First call | Passes |
| Signature validity | 213–216 | `_isValidSignature` requires valid 65-byte EIP-191 | Depends on input |
| **Params not set** | **220** | `require(p.maxPositionSizeUsd > 0, "risk params not set")` | **REVERTS** |
| Max position size | 221 | Never reached | — |
| Confidence ≥ 0 | 222 | Never reached | — |
| Min confidence | 223 | Never reached | — |
| Leverage check | 229–232 | Skipped (requires `maxPositionSizeUsd > 0 && maxLeverageBps > 0`) | — |
| Daily reset | 238–244 | Never reached | — |
| Max daily trades (100) | 246 | Never reached | — |
| **Daily loss limit** | **247** | `require(dailyLoss < maxDailyLossUsd)` → `dailyLoss < 0` | **Always reverts (uint256 ≥ 0)** |

**Conclusion:** With unset parameters, `logDecision` **always reverts** — first at line 220 (`"risk params not set"`). Even if line 220 were bypassed, line 247 (`dailyLoss < maxDailyLossUsd` → `dailyLoss < 0`) is impossible for a `uint256` and would always revert. **The contract actively blocks all decision logging until `setRiskParams` is called.** It is not passively unprotected; it is actively blocking.

### 1.4 `confidence` type

`confidence` is `int256` (signed 256-bit) in both `TradeDecision` and `DecisionInput` structs (line 23). Therefore `require(input.confidence >= 0, "NEGATIVE_CONFIDENCE")` at line 222 **is a meaningful guard** — negative values are representable and are rejected. Do not describe this as a no-op.

### 1.5 Kill switch

- `killSwitchActive[agent]` defaults to `false`.
- `killSwitchReason[agent]` defaults to `""`.
- `activateKillSwitch(reason)` sets both; `deactivateKillSwitch()` clears both.
- **An inactive kill switch is the normal operating state** for decision logging to proceed. Verifying that the switch *can* be activated and deactivated (operational check) is a readiness requirement; activating it merely to satisfy a checklist is not.

---

## 2. Guard-validation matrix

| # | Scenario | maxPositionSizeUsd | maxDailyLossUsd | minConfidenceBps | killSwitch | Valid signature? | Expected `logDecision` result |
|---|----------|-------------------|-----------------|-----------------|------------|-----------------|------------------------------|
| 1 | Unset (current testnet state) | 0 | 0 | 0 | false | Yes | **REVERT** `"risk params not set"` |
| 2 | Set, valid trade within limits | >0 | >0 | >0 | false | Yes | **PASS** — decision logged |
| 3 | Set, size exceeds cap | >0 | >0 | >0 | false | Yes | **REVERT** `"EXCEEDS_MAX_POSITION_SIZE"` |
| 4 | Set, confidence below min | >0 | >0 | >0 | false | Yes | **REVERT** `"BELOW_MIN_CONFIDENCE"` |
| 5 | Set, daily loss exceeded | >0 | >0 | >0 | false | Yes | **REVERT** `"DAILY_LOSS_LIMIT_EXCEEDED"` |
| 6 | Kill switch active | any | any | any | true | Yes | **REVERT** `"KILL_SWITCH_ACTIVE"` |
| 7 | Invalid signature | any | any | any | false | No | **REVERT** `"invalid signature"` |
| 8 | Duplicate decisionId | any | any | any | false | Yes | **REVERT** `"decision already logged"` |
| 9 | Negative confidence | any | any | any | false | Yes | **REVERT** `"NEGATIVE_CONFIDENCE"` |
| 10 | Max trades (100/day) exceeded | any | any | any | false | Yes | **REVERT** `"MAX_TRADES_EXCEEDED"` |
| 11 | Unset, hypothetically bypassing line 220 | 0 | 0 | 0 | false | Yes | **REVERT** `"DAILY_LOSS_LIMIT_EXCEEDED"` (`dailyLoss < 0` impossible) |

**Rows 1 and 11 are the key safety finding:** zero decisions can be logged until `setRiskParams` is called with non-zero `maxPositionSizeUsd` and `maxDailyLossUsd`. The contract is not "missing protection" — it is enforcing a total logging block.

### Guards enforced regardless of parameter state

| Guard | Enforced when params unset? | Mechanism |
|-------|---------------------------|-----------|
| Kill switch | Yes | Line 196, checked before params |
| Signature validity | Yes | Lines 213–216; EIP-191, 65 bytes, v=27/28, non-malleable s (EIP-2) |
| Duplicate decisionId | Yes | Line 199 |
| Negative confidence | Yes | Line 222 (`int256` can be negative) |
| Max trades per day (100) | Yes | Line 246, hard-coded |
| Per-asset volume cap | Only if cap > 0 | Unset (0) = skipped, backward compatible |

---

## 3. Execution enforcement (`recordExecution`, lines 306–363)

| Check | Line | Behavior |
|-------|------|----------|
| Decision must be logged first | 313 | `require(decisionLogged[decisionId], "decision not logged")` |
| Not already executed | 314 | `require(!decisionExecuted[decisionId], "execution already recorded")` |
| Caller is decision owner | 326 | `require(d.agent == msg.sender, "not decision owner")` — prevents any EOA from forging a fill on another agent's decision |
| Direction-aware P&L | 338–351 | Long: loss if `fillPrice < entryPrice`. Short: loss if `fillPrice > entryPrice`. Corrects the original bug that treated any fill below entry as a loss (only true for longs). |
| Loss added to dailyLoss | 349 | `dailyLoss[msg.sender] += lossUsd` only on successful fills with `lossUsd > 0`. Uses ceiling division rounding up. |
| Event emitted | 362 | `TradeExecuted(decisionId, msg.sender, fillPrice, fillSizeUsd, feeUsd, success)` |

**Authorization note:** `onlyAgent` proves `msg.sender == tx.origin` (EOA, no relayer) but does **not** prove ownership of a specific decision. The additional `d.agent == msg.sender` check at line 326 closes this gap.

---

## 4. Outstanding edge cases

### 4.1 Boundary values
- `maxPositionSizeUsd = 0` after initial set: impossible via `setRiskParams` (tightening-only from any non-zero value; setting 0 after a positive value would be "loosening" only if old value was 0, which can't happen after first set). First call with 0 reverts at line 220 on next `logDecision`.
- `minConfidenceBps = 0`: valid first set; line 222 (`confidence >= 0`) passes all non-negative confidences, line 223 (`confidence >= 0`) passes everything. Effectively no confidence floor.
- `maxLeverageBps = 0`: leverage check at line 229 requires `maxPositionSizeUsd > 0 && maxLeverageBps > 0`, so skipped. Effectively no leverage cap.
- `sizeUsd = 0`: passes `maxPositionSizeUsd` check (0 ≤ anything), passes leverage (0 ≤ maxAllowed). A zero-size decision can be logged.

### 4.2 UTC-day rollover
- Line 238: `today = block.timestamp / 1 days` — Solidity `1 days` = 86400 seconds.
- When `today > dailyBucket[msg.sender]`, counters (`dailyTrades`, `dailyVolume`, `dailyLoss`) reset to 0.
- **Edge case:** If `block.timestamp` is manipulated or the chain has irregular block times, the bucket boundary shifts with block time, not wall-clock UTC. A decision logged at 23:59:59 and one at 00:00:01 are in different buckets only if a block boundary crossed between them.
- Per-asset buckets (`assetDailyBucket`) follow the same pattern independently.

### 4.3 Per-asset caps
- `assetVolumeCap[agent][assetHash]` defaults to 0 = unset = global checks only.
- `setAssetCap` requires `capUsd > 0` on first set; subsequent sets must tighten (`capUsd <= current`).
- Cap checked at line 258–263: `assetDailyVolume + input.sizeUsd <= cap`. Reverts `"ASSET_CAP_EXCEEDED"` if exceeded.
- Unset caps (0) are skipped — backward compatible, no per-asset restriction.

### 4.4 Parameter tightening
- Once `setRiskParams` is called with non-zero values, all subsequent calls must tighten (position ≤ old, loss ≤ old, leverage ≤ old, confidence ≥ old).
- **A configuration mistake cannot be undone by restoring original settings** — loosening is permanently forbidden. This is a deliberate design ("tightening-only") but means any post-deployment parameter error requires a new contract deployment or agent address to correct.

---

## 5. Release criteria

Before treating the contract as production-ready or calling `setRiskParams`:

| # | Criterion | Evidence required |
|---|-----------|-------------------|
| 1 | Contract deployed on expected chain | `w3.eth.chain_id == 1952`, contract code present at address, address matches manifest/vercel.json |
| 2 | View functions callable | `getDecisionCount`, `getExecutionCount`, `getAgentDailyStats`, `agentRiskParams`, `killSwitchActive` all return without error |
| 3 | Risk parameters configured | `setRiskParams` called with non-zero `maxPositionSizeUsd` and `maxDailyLossUsd`; `agentRiskParams[agent]` shows non-zero values |
| 4 | Kill switch operational | `activateKillSwitch` and `deactivateKillSwitch` tested (can be toggled); inactive in normal operation |
| 5 | Gas estimates obtained | `eth_estimateGas` for `logDecision` with valid signed input; `recordExecution` with a logged decision. Requires a signed transaction — needs `AGENT_WALLET_PRIVATE_KEY`, out of scope pre-gate |
| 6 | Ledger reconciliation documented | On-chain `dailyLoss` vs off-chain equity-delta differences documented; disagreement handling specified |
| 7 | G1 passed | `scripts/mem_e_g1_report.py` prints "G1: PASS" (≥7 days, ≥30 round-trips, net positive) |
| 8 | Release gate approved | Recorded gate decision authorizing configuration |

**None of criteria 3–7 may be satisfied before G1 passes.** The contract is deployed but not operationally ready as a risk enforcement layer until `setRiskParams` is called with non-zero values — and that call is a state-changing, signer-gated action that must wait for the release gate.

---

## 6. Testability note (gas estimation)

`eth_estimateGas` simulates execution without committing a transaction — it does not inherently require a state-changing call or private key. The current estimate failures (`"execution reverted: no data"` for `logDecision`, `"decision not logged"` for `recordExecution`) are caused by invalid inputs or missing prerequisite state (empty signature, zero decisions), not by the estimation method itself.

**To obtain meaningful gas estimates without touching the existing testnet contract:**
- Deploy a disposable local copy of `TradeAuditTrail.sol` (Anvil/Hardhat fork or fresh deployment).
- Call `setRiskParams` with test values.
- Call `logDecision` with a valid EIP-191 signature (can be generated locally without a real key for estimation purposes).
- Then `eth_estimateGas` for both `logDecision` and `recordExecution` will return meaningful values.

This is optional infrastructure work and does not affect the existing testnet contract.

---

**Status:** Source review complete. Ready for ledger reconciliation documentation (deliverable 2) and post-G1 configuration procedure (deliverable 3).
