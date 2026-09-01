# Complimentary: Seedless Design Patterns Worth Stealing

*Compiled from the Seedless wallet codebase (C:\Users\Henoch\Documents\Programming Folder\seedless)
*For the Tarstrade project — patterns adaptable to pooled vault + depositor UI models
*This is not a dependency integration; it's a curated pattern review for cross-project inspiration.

---

## 1. Threat Model as Product Feature (SECURITY.md §4)

### What Seedless does
Seedless maintains a comprehensive `SECURITY.md` with an explicit threat model section that covers:
- Assets being protected (user funds, stealth-address master seeds, MPC user shares, session signer keys, passkeys in OS secure enclave)
- Adversaries considered (malicious recipients, network-position adversaries, malicious dApps, compromised local device storage)
- Known gaps (automated test coverage is partial, several flows evolved quickly through hackathon period, on-chain controller not yet mainnet-hardened, not yet independently audited)

**Why it's worth stealing for Tarstrade**: Most DeFi vaults have zero publicly documented threat models. Having one built into the codebase signals to external depositors that the team has consciously analyzed attack surfaces rather than hoping for the best.

### Tarstrade adaptation
Add a `SECURITY.md`-style section to `TradingVault.sol`'s naturalization ADR that covers:
- Custodial vs non-custodial boundaries (pooled vault = non-custodial at depositor level, but agent controls trading)
- Offramp KYC trust assumptions (if/when fiat integration is added)
- Known gaps documented upfront (no external audit yet, on-chain controller hardening status)
- Coordinated disclosure policy for external researchers

**File to reference**: `Tarstrade\docs\DESIGN-external-vault.md` §4 (already exists but could be expanded with this pattern)

---

## 2. "Three Questions" Progressive Disclosure UI

### What Seedless does
Seedless's `AppContent.tsx` (2479-line WalletScreen) is a god component, but the underlying product model maps cleanly to Tarstrade's `/depositor` surface:

| Seedless screen | Tarstrade equivalent |
|---|---|
| **Hero** (one-sentence safety claim + "audit on-chain" link) | Hero — one-sentence safety claim + on-chain verification link |
| **Stats** (TVL, return, depositor count, share price) | Stats — TVL, return (period), share price, depositor count |
| **Deposit** (wallet connect → USDT → shares) | Deposit — wallet connect → USDT → shares (MIN_DEPOSIT/MAX_TVL surfaced) |
| **Withdraw** (request → finalize, settlement window) | Withdraw — request → finalize (settlement window shown) |
| **My position** (shares, value in USDT, since-date) | My position — shares, value in USDT, since-date |
| **Verify** (links to on-chain reads) | Verify — on-chain reads (decisions/packages/risk) |

**Why it's worth stealing for Tarstrade**: The "five-screen progressive disclosure" model (Hero/Stats/Deposit/Withdraw/My position/Verify) is a clean, discoverable UI architecture that prevents the "2479-line god component" problem. Each screen is ~200-300 lines instead of one monolithic screen.

### Tarstrade adaptation
Replace the current operator-dashboard-as-depositor-UI approach with explicit five-screen decomposition:

1. **`/depositor/hero`** — one sentence + on-chain verification link
2. **`/depositor/stats`** — TVL, return, share price (from vault reads)
3. **`/depositor/deposit`** — wallet connect → deposit → shares (MIN_DEPOSIT/MAX_TVL reverts)
4. **`/depositor/withdraw`** — requestWithdraw → finalizeWithdraw (settlement window)
5. **`/depositor/my-position`** — balanceOf(me) × totalAssets()/totalSupply()
6. **`/depositor/verify`** — links to TradeAuditTrail.sol reads (decisions, packages, risk hashes)

**File to create**: `Tarstrade\docs\design\depositor-ui-patterns.md` (new)

---

## 3. Rate Limiting + Anti-Abuse as First-Class Concern

### What Seedless does
Seedless has `src/utils/sendRateLimit.ts` with a per-wallet rate limiter:
- 5 sends/min per wallet via Kora paymaster
- Reserve a slot before submitting (submitting costs the relayer)
- If rate limited: `Alert.alert('Slow down a sec', 'You've hit the limit of 5 sends per minute. Try again in Xs.)`
- Comment explains the "why": "Reserve a slot before firing — submitting is what costs the relayer. Caps one wallet at 5 sends/min so a leaked paymaster key (reused on a single wallet) can't spam Kora."

**Why it's worth stealing for Tarstrade**: Most smart vault contracts have no rate limiting on withdrawals beyond `MAX_TVL`. Adding explicit per-depositor rate limits (or per-settlement-window limits) is a cheap anti-abuse measure that protects both the agent and depositors.

### Tarstrade adaptation
Add to `TradingVault.sol` or the accompanying off-chain code:

```solidity
// Per-depositor withdrawal rate limit (v1.1)
event WithdrawalRateLimited(
    address indexed depositor,
    uint256 requestId,
    uint256 retryAfterSeconds
);

function requestWithdraw(uint256 shares)
    external
    returns (uint256 requestId)
{
    // Check rate limit: max 1 withdrawal per 8h per depositor
    uint256 lastWithdrawal = withdrawalTimestamps[depositor];
    uint256 timeSinceLast = block.timestamp - lastWithdrawal;
    require(
        timeSinceLast >= 28800 || msg.sender == owner,
        "WithdrawalRateLimited"
    );
    withdrawalTimestamps[depositor] = block.timestamp;
    // ... rest of function
}
```

**File to reference**: `Tarstrade\docs\DESIGN-external-vault.md` §5.2 (withdrawal flow) — expand with rate limit discipline

---

## 4. Session Expiry + Graceful Degradation

### What Seedless does
Seedless's `src/utils/session.ts` implements 30-min session slots:
- `SESSION_SLOT_DURATION = 4500n` (~30 minutes, 4500 slots × 400ms each)
- Session keys stored in SecureStore with `WHEN_UNLOCKED_THIS_DEVICE_ONLY`
- `getActiveSession()` fast-path: local-clock estimate of current slot, skip RPC when session is comfortably in-window
- Safety buffer: 120 slots (~48s) before re-probing getSlot
- Session auto-expires after window; users can "extend session" with biometric re-auth
- `clearSession()` parallel deletes (secret key + PDA + expiry)

**Why it's worth stealing for Tarstrade**: Session management is a first-class concern in Tarstrade's agent-onboarding flow. Currently, the agent-auth relationship is implicit (operator keys = full control). Explicit session windows with auto-expiry + biometric re-auth would give depositors more confidence and give the agent a smaller attack surface.

### Tarstrade adaptation
Add session windows to the depositor onboarding:

1. **On first deposit**: session auto-creates, 30-min slot starts
2. **Every send/operation**: check `getActiveSession()` — if within window, sign locally (no biometric prompt); if expired, prompt biometric for new session
3. **Session expiry**: after 30 mins of inactivity, session auto-clears; next operation creates fresh session
4. **"Extend session" button** in My position screen — biometric-confirmed, extends 30 more mins
5. **Stale session auto-clear**: off-chain cleanup every hour removes sessions > 24h old

**File to create**: `Tarstrade\docs\design\session-management-patterns.md` (new)

---

## 5. Changelog + Version Discipline

### What Seedless does
Seedless maintains `CHANGELOG.md` with entries from 0.4.0-beta (first public mainnet beta) to 0.4.5-beta:
- 0.4.5-beta: Multi-wallet support, any-coin swaps, transaction history, address book, wallet lock, light/dark themes
- 0.4.4-beta: Balances refresh on incoming payment, toast banner clipping fix, history screen hang fix, RPC noise reduction
- 0.4.2-beta: Burner wallets now support SPL tokens, stealth address QR in dark mode, shortened private mode copy, retry spam fix
- 0.4.1-beta: Multi-token sends (SOL, USDC, SEED), token registry, swap token picker, preflight balance check, shared loading/success/error screens
- 0.4.0-beta: First public mainnet beta — passkey login, gasless sends/swaps, stealth addresses, burner wallets, private sends

Plus `.env.example` with clear env var documentation, `package.json` with `typecheck`/`test` scripts, and `DEPENDENCY-AUDIT.md` with explicit conflict warnings.

**Why it's worth stealing for Tarstrade**: Many DeFi projects have CHANGELOGs that are auto-generated or nonexistent. Seedless's manual, human-readable changelog with feature-level detail is a maturity signal that builds trust with external depositors.

### Tarstrade adaptation
Ensure every release (especially vault contract upgrades) has a CHANGELOG entry following this format:
- Version (semver)
- One-line summary of what changed
- List of new features (with screen/flow references)
- List of bug fixes (with test references)
- List of non-goals (reiterate what didn't change)
- Explicit "this release does NOT change" section (like Seedless's "Below this the fees make the transfer pointless" MIN_OFFRAMP_NGN constraint)

**File to reference**: `Tarstrade\ADR` folder or `Tarstrade\docs\adr` — add changelog discipline ADR

---

## 6. Optional Offramp Module Pattern

### What Seedless does
Seedless's offramp (USDC → naira via Cloudflare Worker + partner API) is an **optional module**, not core to the wallet:
- Core wallet: passkey login, gasless SOL/USDC sends, private sends via Umbra ✅
- Offramp module: ⚠️ Requires KYC (BVN/NIN) ⚠️ Fiat settlement via partner ⚠️ **Optional — skip if you only want on-chain**
- Documented trust boundary in SECURITY.md §4: "Offramp relies on Cloudflare Worker + partner API key. This is a custodial integration distinct from the non-custodial passkey wallet."
- Offramp KYC flow lives in `src/utils/offramp.ts` (525 lines) — separate from core transfer logic

**Why it's worth stealing for Tarstrade**: If/when Tarstrade adds fiat on/off-ramps, the "optional module" pattern keeps the core vault pure (USDT → shares → agent trades) while the offramp is a separate, KYC'd integration. This preserves the "not your keys, not your coins" ethos for the core product.

### Tarstrade adaptation
Model any fiat integration as an "offramp module" with these boundaries:

```
Core vault (always active):
  ✅ USDT deposit → shares
  ✅ Agent trades one pot of capital
  ✅ On-chain audit trail (TradeAuditTrail.sol)
  ✅ Pooled shares (ERC-4626 style)

Offramp module (opt-in, disabled by default):
  ⚠️ KYC validation (BVN/NIN, per partner requirements)
  ⚠️ Fiat settlement (naira, USD, etc. via partner API)
  ⚠️ Depositor can opt-out at any time
  ⚠️ Separate trust assumption from core vault
```

**File to create**: `Tarstrade\docs\design\offramp-module-patterns.md` (new)

---

## 7. On-Chain Audit Trail as Product Differentiator

### What Seedless does
Seedless's `SECURITY.md` §2 explicitly lists what's in-scope vs out-of-scope:
- **In scope**: Transaction construction/broadcast, session signer lifecycle, on-chain authorization program, private transfer integration, ZK proof integration, cross-chain client, value-movement screens, stealth and token-detection helpers
- **Out of scope**: LazorKit (passkey auth + Kora paymaster), Umbra (stealth protocol), Jupiter (swaps), Alchemy (RPC), Ika (MPC network), also outside policy: app store compliance, UI visual issues

And §3 trust boundaries table clearly maps what Seedless implements vs what it delegates:
- Passkey signing → LazorKit
- Gas sponsorship → LazorKit's Kora paymaster
- Stealth-address protocol → Umbra
- Swap routing → Jupiter
- RPC → Alchemy
- MPC and cross-chain → Ika
- Transaction construction, session-key lifecycle, ATA handling, intent routing → **Seedless**
- Multi-chain authorization via on-chain controller → **Seedless**

**Why it's worth stealing for Tarstrade**: Most vault projects don't document this level of trust boundary clarity. Having an explicit "what we own vs what we delegate" table in the vault's naturalization ADR would be a huge trust signal for external depositors.

### Tarstrade adaptation
Add to `TradingVault.sol` naturalization ADR a trust boundary table mirroring Seedless's format:

| Boundary | Trust assumption | Implemented by |
|---|---|---|
| Vault contract accounting | ERC-4626 share math, `MIN_DEPOSIT`/`MAX_TVL` enforcement | `TradingVault.sol` |
| Agent trading decisions | Risk gate, curator profiles, Kelly sizing, regime throttle | `src/agent.py`, `src/curator.py` |
| On-chain audit trail | `packageId`, `riskHash`, `dailyLoss`/`dailyTrades` counters | `TradeAuditTrail.sol` |
| Reconciliation | Operator-attested balance vs actual OKX account | Off-chain agent reports |
| Depositor withdrawals | Two-step `requestWithdraw` → `finalizeWithdraw` | `TradingVault.sol` |
| Kill switch | Operator-only, never surface on `/depositor` | Agent dashboard only |

**File to create**: `Tarstrade\docs\ADR\0004-trust-boundaries.md` (new)

---

## Summary: What Tarstrade Can "Steal" from Seedless Tomorrow

| Pattern | Effort to Adapt | Trust Value | Priority |
|---|---|------|---|
| Threat model as product feature | Low (doc addition) | High (depositor confidence) | P0 |
| Five-screen progressive disclosure | Medium (UI refactor) | High (usability) | P0 |
| Rate limiting on withdrawals | Low (solidity + off-chain) | Medium (abuse prevention) | P1 |
| Session expiry + biometric re-auth | Medium (onboarding flow) | Medium (attack surface reduction) | P1 |
| Changelog + version discipline | Low (process change) | Medium (maturity signal) | P1 |
| Optional offramp module pattern | Low (architecture doc) | High (custodial boundary clarity) | P0 |
| Explicit trust boundary table | Low (ADR addition) | High (depositor trust) | P0 |

**Bottom line**: The highest-impact, lowest-effort patterns are (P0) the threat model documentation, the five-screen depositor UI decomposition, the optional offramp module pattern, and the explicit trust boundary table. These four alone would materially improve Tarstrade's depositor-facing transparency without touching the core vault contract or agent logic.

*End of complimentary patterns review.*