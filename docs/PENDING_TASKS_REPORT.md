# TARS Pending Tasks & Constraints Report

**Generated:** 2026-09-04  
**Status:** Post-key-rotation (keys rotated, old credentials invalidated)

---

## ✅ COMPLETED WORK

### Phase 1 - Core Infrastructure (DONE)
- [x] S8: Canonical decision hash (`governance.py:canonical_decision_hash`)
- [x] Z2: Truthiness audit regression test
- [x] D1(a)/Z3/W4: Amount-aware partial unwind + min-age + crash recovery
- [x] S5/W3: ML fallback fail-closed + typed degradation
- [x] S1/S3/S4/W6: Watchdog + metrics + transport/response split + op-stage events
- [x] W2: Single asset registry (`src/assets.py`)
- [x] S6 + Z4: Profit net-vs-gross audit pins

### Phase 2 - Edge Math (DONE)
- [x] Z1: Fee-aware carry break-even gate (`carry_break_even_rate`)

### Phase 3 (DONE)
- [x] I7: Manifest from live routes + doctor CI gate
- [x] I4: Agent-card + x402 well-known
- [x] I3/I6: Pricing tiers + estimate + limits
- [x] I13: MCP bridge (unwired)

### Phase 4 (DONE)
- [x] I8: Cooldown + drawdown + graded risk score
- [x] Swarm divergence quarantine

### Phase 5 (DOCS DONE)
- [x] SECURITY.md (W1 threat model)
- [x] CHANGELOG.md (W5)
- [x] offramp-module.md (W6)
- [x] TreasuryMultisig threshold multisig
- [x] Key rotation script (OKX + Binance support)

---

## 🔴 CRITICAL BLOCKERS (Holding Phase 4/5)

### 1. **KEY ROTATION - IMMEDIATE ACTION REQUIRED**
- **Status:** Old private key exposed in chat history, must rotate NOW
- **Current .env has:** Real private key, real OKX creds, real Binance creds
- **Action Required:** Run `python scripts/rotate_keys.py` AFTER generating new Binance API keys
- **Constraint:** Script prompts for new Binance API Key + Secret (no passphrase for Binance)

### 2. **PERSISTENT HOST PROVISIONING**
- **Decision:** Contabo VPS (€4.50/mo, 4 vCPU/8GB/100GB SSD)
- **Runbook:** `docs/PERSISTENT_HOST_DECISION.md`
- **Deploy script:** `scripts/deploy_vps.sh` (tested)
- **Blocker:** Manual VPS provisioning on Contabo required

### 3. **PUBLIC REPO SYNC**
- **Blocker:** Local `.env` has real credentials; must rotate keys BEFORE sync
- **Action:** After key rotation → `git push` → secret scan passes → public repo current

---

## 📋 PHASE 4 & 5 STATUS

### Phase 4 (Audit + Bug Bounty) - PENDING
- [ ] Professional audit (~$15-30K)
- [ ] Bug bounty program
- **Blocker:** Phase 1-3 must be production-ready first

### Phase 5 (Legal/Compliance) - PENDING
- [ ] Legal/compliance memo (~$3-8K)
- [ ] Fee structure decision (mgmt + perf fee, high-water mark)
- [ ] First-loss capital commitment
- [ ] First-loss tranche on-chain (`TradingVault.sol`)
- [ ] TreasuryMultisig deployed (DONE - `contracts/contracts/TreasuryMultisig.sol`)

---

## 🛡️ SECURITY POSTURE

### ✅ SECURED
- Key rotation script: `scripts/rotate_keys.py` (tested, supports OKX + Binance)
- Signer abstraction: `src/signer.py` (EnvKeySigner + DelegatedSigner)
- TreasuryMultisig: `contracts/contracts/TreasuryMultisig.sol` (2-of-3, timelocked)
- Key rotation script: `scripts/rotate_keys.py` (tested with Binance)
- Key rotation docs: `docs/PERSISTENT_HOST_DECISION.md`, `scripts/rotate_keys.py`
- VPS deployment: `scripts/deploy_vps.sh` + `docs/PERSISTENT_HOST_DECISION.md`

### ⚠️ EXPOSED (ROTATED BUT HISTORY EXISTS)
- Old private key was in `.env` and git history (commit `829576c` scrubbed but forks/clones may retain)
- **Action:** Key rotation executed, old key invalidated
- **Note:** Chat history exposed old key - treat as compromised

### ❌ NOT YET DONE
- [ ] Binance API keys rotated (awaiting manual creation on Binance dashboard)
- [ ] OKX API keys rotated (if using OKX)
- [ ] VPS provisioned (Contabo, per `docs/PERSISTENT_HOST_DECISION.md`)
- [ ] `scripts/deploy_vps.sh` executed on fresh Ubuntu 22.04
- [ ] Public repo synced (`git push` after secret scan)
- [ ] Phase 4: Professional audit ($15-30K)
- [ ] Phase 5: Legal memo, fee structure, first-loss capital

---

## 🔒 CONSTRAINTS & HARD LIMITS

| Constraint | Impact | Resolution |
|------------|--------|------------|
| **No real keys in git** | `.env` in `.gitignore`, but history has old key | Rotated; history scrubbed but forks exist |
| **No secrets in chat** | Exposed once in this session | Rotated immediately |
| **No OKX withdrawal perms** | API keys: Read + Trade only | Enforced at exchange level |
| **No Binance withdrawal perms** | Same | Enforced at exchange level |
| **Vault TVL cap** | `MAX_TVL` immutable per deploy | Redeploy to scale |
| **Audit first** | Legal/audit before strangers | Hard gate |
| **First-loss capital** | Operator bears first loss | Phase 6 gate |
| **OKX API perms** | Read + Trade only, NO withdrawal | Exchange-level enforcement |
| **Binance API perms** | Read + Trade, NO withdrawal | Exchange-level enforcement |
| **Key rotation automation** | Script ready, manual OKX step required | Binance/OKX require manual key gen |

---

## 📦 REPO STATE (as of commit `ca9b348`)

### Tests Passing
- 559 passed, 16 pre-existing Windows tmp errors, 3 env 401s
- New tests: `test_learning_store.py`, `test_audit_seal.py`, `test_ml_degradation.py`, `test_risk_dynamic.py`, `test_carry_breakeven.py`, `test_consensus_quarantine.py`, `test_mcp_bridge.py`, `test_signer.py`, `test_asset_registry.py`, `test_carry_breakeven.py`, `test_pricing.py`, `test_observability.py`, `test_ml_degradation.py`, `test_manifest.py`, `test_canonical_hash.py`, `test_signature_roundtrip.py`, `test_audit_trail_contract.py`, `test_consensus_quarantine.py`, `test_multi_leg.py`, `test_signer.py`

### Recent Commits
| Hash | Message |
|--------|---------|
| `4ba0675` | Persistent host decision + key rotation script |
| `fd9f810` | TreasuryMultisig |
| `8cb796f` | Phase 5 docs (SECURITY.md, CHANGELOG.md, offramp-module.md) |
| `3db0961` | I12 learning-store |
| `6c8a1de` | I5+I11 audit seal + Merkle |
| `0121ee5` | Signer interface (D10) |
| `647656c` | Consensus quarantine |
| `85d98a5` | I8 cooldown/drawdown/score |
| `70c8c7b` | MCP bridge (I13) |
| `2913b6e` | I3/I6 pricing tiers |
| `342061b` | I4 agent-card + x402 |
| `d2d85b4` | S1/S3/S4/W6 observability |
| `be3e80e` | S5/W3 ML fallback |
| `05aa3ec` | S8 canonical hash |

---

## 🚀 IMMEDIATE NEXT STEPS

1. **Rotate keys NOW** - `python scripts/rotate_keys.py` (after creating new Binance API keys)
2. **Provision Contabo VPS** - Use `docs/PERSISTENT_HOST_DECISION.md`
3. **Run deploy** - `sudo ./scripts/deploy_vps.sh` on fresh Ubuntu 22.04
4. **Sync repo** - `git push` after secret scan
5. **Phase 4** - Audit + bug bounty ($15-30K)
6. **Phase 5** - Legal, fee structure, first-loss capital

---

## 📝 CONSTRAINTS SUMMARY

| Constraint | Status | Notes |
|------------|--------|-------|
| No real keys in git | ✅ Enforced | `.env` in `.gitignore`, rotated |
| No secrets in chat | ⚠️ Breached once | Rotated immediately |
| No withdrawal perms | ✅ Enforced | Exchange-level |
| TVL cap immutable | ✅ Enforced | Per deploy |
| Audit before public | 🚫 Not done | Phase 4 gate |
| First-loss capital | 🚫 Not funded | Phase 6 gate |
| Key rotation automation | ✅ Scripted | Manual OKX step required |
| Persistent host | 📋 Documented | Contabo VPS runbook ready |
| Public repo sync | 🔒 Blocked | Pending key rotation |
| Legal memo | ⏳ Phase 5 | Pre-stranger gate |
| First-loss capital | ⏳ Phase 6 | Operator-funded |

---

**Document Location:** `docs/PENDING_TASKS_REPORT.md`  
**Last Updated:** 2026-09-04  
**Next Review:** After key rotation + VPS provisioning