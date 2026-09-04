# Optional Offramp Module (W6)

**Status**: DESIGN — optional, opt-in, disabled by default. Never part of the core vault.

---

## Purpose

If/when TARS adds a fiat on/off-ramp, it MUST be a separate, opt-in module with these invariants:

1. **Core vault stays pure** — USDT → shares → agent trades. Offramp is a separate KYC'd integration.
2. **Depositor opts in explicitly** — no silent enrollment, no hidden fees.
3. **Separate trust assumption** — offramp partner has its own KYC/AML, regulatory surface, custody model. The core vault's "don't trust, verify" claim does not extend to the offramp.
4. **Separate blast radius** — an offramp hack/breach/freeze cannot touch vault TVL or the trading agent's OKX account.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        TARS Core Vault                          │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │  Depositor  │───▶│ TradingVault │───▶│  OKX Agent (EOA) │  │
│  │  (USDT)     │    │  (ERC-4626)  │    │  (Trading)       │  │
│  └─────────────┘    └──────────────┘    └──────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌───────────────────────┐
                    │   Offramp Module      │
                    │   (Opt-in, KYC'd)     │
                    │  Shares → USDT → Fiat │
                    └───────────────────────┘
                              │
                              ▼
                       ┌─────────────────┐
                       │  Fiat Rail      │
                       │  (Bank/Partner) │
                       └─────────────────┘
```

---

## Depositor Flow (Opt-in)

1. **On /depositor UI**: "Add offramp" button → redirects to partner KYC flow.
2. **Partner KYC**: Partner collects BVN/NIN (Nigeria) / KYC docs (global). TARS never sees PII.
3. **Link created**: Partner returns a `partner_user_id` → stored in `depositor_offramp` mapping (vault storage or off-chain DB).
3. **Withdraw with offramp**:
   - User clicks "Withdraw to Bank" → `requestWithdraw(shares, offramp=true)`
   - Vault burns shares, reserves USDT, creates `WithdrawalRequest` with `offramp_partner_id`
   - After settlement window: partner receives `offramp_fulfill(requestId)` call → executes fiat payout
   - On success: partner calls `offramp_fulfill` → vault marks `finalized` + emits event
   - On failure/timeout: `expireWithdrawal` restores shares (same as core)

---

## Vault Contract Interface (Offramp Extension)

```solidity
// Optional extension — only loaded if OFFRAMP_ENABLED=true
contract TradingVaultOfframp {
    struct OfframpPartner {
        address partner;           // Partner's EOA (multisig)
        string name;               // "Monnify", "Flutterwave", etc.
        bool active;
        uint256 max_per_tx_usd;    // Partner-level cap
        uint256 daily_limit_usd;   // Partner daily cap
    }

    mapping(uint256 => OfframpPartner) public offrampPartners;
    mapping(address => uint256) public depositorOfframpPartner;

    event OfframpPartnerAdded(uint256 indexed id, address partner, string name);
    event OfframpFulfilled(uint256 indexed withdrawalId, uint256 amount, address partner);
    event OfframpFailed(uint256 indexed withdrawalId, string reason);

    function addOfframpPartner(address partner, string calldata name,
                               uint256 maxPerTx, uint256 dailyLimit)
        external onlyOwner { ... }

    function setDepositorOfframp(uint256 partnerId) external { ... }

    function requestWithdrawWithOfframp(uint256 shares, uint256 partnerId)
        external returns (uint256 requestId) { ... }

    function offrampFulfill(uint256 withdrawalId, uint256 amount)
        external { ... } // called by partner's multisig
}
```

---

## Partner Requirements (Non-Negotiable)

| Requirement | Rationale |
|-------------|-----------|
| **Regulated entity** (CBN/Money Service Business license) | Legal compliance, depositor protection |
| **1:1 USDT:NGN/USD backing** | No fractional reserve on partner side |
| **SLA: 99.5% uptime, <5min payout** | Depositor experience |
| **Audit trail API** | TARS can verify partner fulfillment independently |
| **Segregated funds** | Partner's operational float ≠ customer float |
| **Insurance/guarantee** | Depositor protection if partner fails |
| **Real-time webhook** | TARS gets instant fulfillment/failure notification |

---

## Partner Integration (Example: Monnify)

```typescript
// TARS side: initiate offramp withdrawal
async function requestWithdrawOfframp(shares: bigint, partnerId: number) {
  const requestId = await vault.requestWithdrawWithOfframp(shares, partnerId);
  // Vault: burns shares, reserves USDT, emits WithdrawalRequested(offramp=true)
  return requestId;
}

// Partner side: webhook receives { withdrawalId, amount, depositor, partnerUserId }
// Partner executes NGN payout via NIP transfer
// Partner calls back:
await vault.offrampFulfill(withdrawalId, amount, { from: partnerMultisig });
// Vault: verifies partner signature, transfers USDT, marks finalized
```

---

## Security Boundaries

| Boundary | Enforcement |
|----------|-------------|
| **Vault never holds fiat** | USDT only; offramp is separate legal entity |
| **Partner cannot access vault TVL** | `offrampFulfill` only transfers exact `usdtOut` for a specific `requestId` |
| **Depositor can opt out anytime** | `requestWithdraw` without `offramp=true` → standard settlement window |
| **Partner cannot rehypothecate** | USDT moves: Vault → Partner → Depositor bank. No vault exposure. |
| **Audit trail** | Every offramp action logged to `TradeAuditTrail` via `logDecision` with `strategy="offramp_fulfill"` |

---

## Depositor UI (Depositor Surface)

```
┌─────────────────────────────────────────┐
│ 💰 Withdraw                             │
├─────────────────────────────────────────┤
│ Amount: 100 USDT (100 shares)           │
│ Method:                                 │
│   [ ] Standard (8h settlement)          │
│   [✓] Express → Monnify (instant NGN)   │
│                                         │
│ Partner: Monnify (CBN licensed)         │
│ Max per tx: ₦50M  │  Daily: ₦200M       │
│ Estimated: 100 USDT → ₦85,000           │
│                                         │
│ [Withdraw to Bank]                      │
└─────────────────────────────────────────┘
```

---

## Security Properties

| Property | Guarantee |
|----------|-----------|
| **No silent enrollment** | `requestWithdraw(offramp=true)` requires explicit UI action |
| **No hidden fees** | Price shown before confirm; partner fee included in estimate |
| **Partner can't overcharge** | `max_per_tx_usd` + `daily_limit_usd` enforced on-chain |
| **No silent fallback** | If partner fails → `expireWithdrawal` restores shares (standard path) |
| **Auditability** | Every offramp action in `TradeAuditTrail` with `strategy="offramp_fulfill"` |

---

## Deployment Checklist

- [ ] Partner KYC/AML verified
- [ ] Partner multisig address registered on-chain
- [ ] Partner API keys in vault env (not code)
- [ ] `X402_MAX_USD_PER_CALL` covers max offramp fee
- [ ] `X402_PREMIUM_PER_MIN` allows partner callback rate
- [ ] Integration test: deposit → offramp withdraw → fulfill → expire fallback
- [ ] Load test: 100 concurrent offramp requests
- [ ] Chaos test: partner endpoint down → `expireWithdrawal` restores shares
- [ ] Legal: partner agreement signed, TARS offramp addendum signed

---

## Future: Native On-ramp (Phase 8)

When Phase 8 mobile app ships (Nigeria stack: NIP virtual account + USSD + OPay/PalmPay wallet), the offramp module becomes the **inbound rail** too:

```
User → NIP/USSD/OPay → Partner → USDT mint → Vault.deposit() → shares
```

Same partner, reverse flow. Vault stays pure.