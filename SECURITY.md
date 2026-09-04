# TARS Security Model & Threat Model

**Status**: Product feature, not compliance checkbox. This document is part of the deposit flow — depositors can read it at `/depositor/security`.

---

## Assets Protected

| Asset | Custody Model | Risk if Compromised |
|-------|---------------|---------------------|
| Depositor USDT (vault TVL) | Pooled, ERC-4626 shares | Total loss of depositor capital |
| Agent signing key (`AGENT_WALLET_PRIVATE_KEY`) | Custodial (env) — `TradingVault` agent EOA | Full trading control, but bounded by `RiskGate` + `MAX_TVL` |
| Audit signer key (`OnchainLogger`) | Custodial (env) → **connect-wallet session key (planned)** | False audit entries, but no fund movement |
| Operator OKX API keys | Custodial (env), read+trade only, no withdrawal | Unauthorized trades, but bounded by `RiskGate` |
| X Layer RPC endpoints | Public infrastructure | Incorrect on-chain state, no fund loss |

---

## Adversaries Considered

| Adversary | Capability | Mitigation |
|-----------|------------|------------|
| **Malicious recipient** | Receives funds, tries to trigger reentrancy or confuse vault accounting | Virtual shares + ERC-4626 virtual supply resists donation attack; `nonReentrant` on all mutating functions |
| **Network-position adversary** | MITM on RPC, DNS hijack, BGP hijack | Dual RPC endpoints with failover; chain ID + contract address pinned; kill switch check on every decision |
| **Malicious dApp** | Phishing wallet connect, `wallet_switchEthereumChain` to malicious chain | Chain ID pinned to 1952 (X Layer); contract address pinned in manifest; wallet connect shows exact contract address |
| **Compromised local device storage** | Extracts `.env` or wallet private key | Keys never in code; env vars only; connect-wallet session keys expire (planned); hardware wallet support planned |
| **Malicious operator** (insider) | Tries to extract vault funds via fake `attestTotalAssets`, fake `recordExecution` | `onlyAgent` modifier restricts to single EOA; `MAX_ATTESTATION_DELTA_BPS` caps per-attestation drift; on-chain `dailyLoss` + off-chain equity-delta cross-check |
| **Compromised agent key** | Can submit arbitrary `logDecision` + `recordExecution` | `RiskGate` enforces `max_position_usd`, `max_daily_loss_usd`, `max_leverage`, `min_confidence_bps`, `max_slippage_pct`; kill switch on-chain + off-chain |
| **Network partition / RPC outage** | `okx` CLI calls fail, chain RPC fails | Dual RPC endpoints with automatic failover; `check_auth` endpoint; `dry_run` mode blocks live calls |

---

## Known Gaps (Documented Upfront)

| Gap | Impact | Remediation |
|-----|--------|-------------|
| Automated test coverage partial | Some edge cases in `RiskGate` only hit in integration | `scripts/preflight_check.py` blocks deploy if critical env missing |
| On-chain audit logger not yet audited | Forensic mismatch possible | `src/audit_logger.py` + `TradeAuditTrail.sol` have regression suite (`test_signature_roundtrip.py`) |
| No external audit | Professional audit needed before real capital | Budgeted Phase 4 ($15–30K lean) |
| On-chain reconciliation operator-attested | Operator could misreport `totalAssets` | Phase 2: price-pinged reconciliation; Phase 3: on-chain execution via vault-controlled sub-account |
| `AGENT_WALLET_PRIVATE_KEY` in env | Single point of failure for trade signing | Rotation supported via `proposeAgent`/`acceptAgent` (timelocked); future: threshold sig / MPC |
| No formal fuzz testing | State-space exploration limited | `src/execution` has property tests for slippage/position size; more needed |
| Kill-switch alerting only webhook | If webhook down, no page | `scripts/preflight_check.py` validates `ALERT_WEBHOOK_URL` |

---

## Coordinated Disclosure

If you find a security issue, please report it privately:

- **Email**: security@tars-trade.example (placeholder)
- **PGP**: `0x...` (placeholder)
- **Scope**: All code in this repo, deployed contracts on X Layer (chain 1952), and off-chain agent

We commit to:
- Acknowledge within 48 hours
- Provide fix timeline within 7 days
- Credit reporter (unless anonymous requested)
- No legal action for good-faith research within scope

---

## What Depositors Can Verify Themselves

| Claim | How to Verify |
|-------|---------------|
| "Every trade is logged before execution" | Call `TradeAuditTrail.getRecentDecisions()` — every entry has `decisionId`, `agent`, `asset`, `signal`, `strategy`, `confidence`, `entryPrice`, `sizeUsd`, `timestamp`, `riskHash`, `signature` |
| "Risk gate cannot be overridden" | Read `RiskGate` contract: every check returns `RiskCheckResult(approved=false, code, reason)`; no admin bypass |
| "Kill switch works" | Read `killSwitchActive[agent]` + `activateKillSwitch`/`deactivateKillSwitch` events |
| "My money isn't diluted" | `ERC4626` share math + virtual shares prevents donation attack; `MIN_DEPOSIT`/`MAX_TVL` enforced inside `deposit()` |
| "I can get my money out" | `requestWithdraw` → `finalizeWithdraw` (settlement window max 8h); `expireWithdrawal` if stuck |

---

## Asset List (Scope Boundary)

Only these assets are in the trading universe. Any asset outside this list is rejected by the risk gate (enforced by `RiskGate._is_asset_allowed`):

| Asset | Instrument | Quote | Cohort |
|-------|------------|-------|--------|
| BTC-USDT-SWAP | Perp | USDT | majors |
| ETH-USDT-SWAP | Perp | USDT | majors |
| SOL-USDT-SWAP | Perp | USDT | majors |
| BNB-USDT-SWAP | Perp | USDT | majors |

---

## Compliance Notes

- **No regulated securities**: USDT perp swaps on OKX (unregulated offshore exchange).
- **No fiat on/off-ramp in v1**: USDT only. Offramp is a separate optional module (see `docs/offramp-module.md`).
- **Jurisdiction**: X Layer (chain 1952) — OKX-operated L2. No regulatory opinion expressed.
- **No investment advice**: This is infrastructure code. Depositors decide their own risk.