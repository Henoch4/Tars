# Base → X Layer CCTP Feasibility Note — Book 1 Deposit Inbound

**Date:** 2026-10-28  
**Status:** Research — offline, read-only  
**Boundary:** Phase 9 research (per `EXPANSION_ROADMAP.md` and `mainnet-roadmap.md` Phase 9). Capital deployment blocked until after Book 1 validated quarter on majors.  
**No keys, no state changes, no VPS impact.** All data from Circle's public CCTP documentation.

---

## 1. Executive Summary

**Base → X Layer USDC deposits via CCTP are technically feasible.** Both chains support CCTP V2 (burn-and-mint), both support Fast Transfer (~8 seconds), and both have deployed CCTP contracts on mainnet and testnet. The primary constraint is timing: Standard Transfer takes ~15-19 minutes on both chains (OP Stack L1 finality), while Fast Transfer is ~8 seconds but subject to Circle's global allowance limits.

**Recommendation:** Use CCTP Standard Transfer for initial implementation (no allowance constraints), evaluate Fast Transfer for UX-critical deposits once volume justifies it. Integrate with the existing `TradingVault.sol` on X Layer (deployed at `0x747114F3Adc0338132A1E668128c2004f081cBaf` on testnet).

**Attestation timing** is determined by the **source chain** (Base) only. Circle issues the attestation after Base burn finality: ~8 seconds (Fast Transfer) or ~15-19 minutes (Standard Transfer). The X Layer destination transaction (mint + vault deposit) is a separate on-chain action that occurs after attestation receipt — its timing depends on X Layer block inclusion and gas availability, not another 15-19 minute wait.

---

## 2. CCTP Support Status

Both chains are fully supported by CCTP V2:

| Attribute | Base (domain 6) | X Layer (domain 37) |
|-----------|-----------------|---------------------|
| CCTP V2 support | ✅ | ✅ |
| Source: Standard Transfer | ✅ | ✅ |
| Source: Fast Transfer | ✅ (~8s) | ✅ (~8s) |
| Source: Upfront fees | ✅ | ❌ |
| Destination: Forwarding Service | ✅ | ❌ |
| Supported as CCTP destination | ✅ | ✅ |
| USDC native (Circle-issued) | ✅ | ✅ |

**Key constraint:** X Layer does **not** support Circle's Forwarding Service as a destination. This means automated "deposit → vault" forwarding (where Circle's forwarder contract automatically deposits into TradingVault on receipt) is **not available** for X Layer. The vault contract would need its own `receive`/deposit hook logic, or the operator would need to manually trigger the deposit into the vault after the CCTP mint completes.

---

## 3. Timing Characteristics

Both chains are OP Stack L2s posting state batches to Ethereum L1 (~every 15 minutes). Finality characteristics:

| Source Chain | Transfer Type | Attestation Time |
|--------------|---------------|------------------|
| Base | Fast Transfer | ~8 seconds (1 block) |
| Base | Standard Transfer | ~15-19 minutes (~65 ETH blocks) |

**Base finality determines attestation timing.** Attestation is issued after Base burn finality. X Layer destination processing (steps 3-4 below) is a separate OP L2 block inclusion (~8 seconds each), **not** another 15-19 minute wait.

**Implication:** For a depositor experience, Standard Transfer means ~15-19 minutes from "user burns USDC on Base" to attestation availability, plus X Layer block inclusion for the mint (~8s) and vault deposit (~8s). Total end-to-end is dominated by the Base attestation wait (~15-19 min for Standard Transfer). Fast Transfer (~8 seconds) is available on Base as source but is subject to Circle's global allowance to mitigate reorganization risks.

---

## 4. Contract Addresses

All CCTP V2 contracts use the same address across all EVM chains:

| Contract | Mainnet Address | Testnet Address |
|----------|-----------------|-----------------|
| TokenMessengerV2 | `0x28b5a0e9C621a5BadaA536219b3a228C8168cf5d` | `0x8FE6B999Dc680CcFDD5Bf7EB0974218be2542DAA` |
| TokenMinterV2 | `0xfd78EE919681417d192449715b2594ab58f5D002` | `0xb43db544E2c27092c107639Ad201b3dEfAbcF192` |
| MessageTransmitterV2 | `0x81D40F21F12A8F0E3252Bccb954D722d4c464B64` | `0xE737e5cEBEEBa77EFE34D4aa090756590b1CE275` |
| MessageV2 | `0xec546b6B005471ECf012e5aF77FBeC07e0FD8f78` | `0xbaC0179bB358A8936169a63408C8481D582390C4` |

**USDC addresses:**

| Chain | Mainnet USDC | Testnet USDC |
|-------|---------------|--------------|
| Base | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |
| X Layer | `0xB6CEceAB302E2E4948951eE7843FC24E92933061` | `0xDec90b78111Ba2fc6FC6d84d8B9ec159A2d4b9B3` |

**Existing deployment:** `TradingVault.sol` is deployed on X Layer testnet at `0x747114F3Adc0338132A1E668128c2004f081cBaf`.

---

## 5. Transfer Flow (Base → X Layer)

### Step-by-step:

1. **User deposits USDC on Base** — User sends USDC to CCTP `TokenMessengerV2.depositForBurn()` on Base, specifying X Layer (domain 37) as destination and their X Layer address as recipient.

2. **Burn on Base** — USDC is burned on Base. Circle's attestation service observes the burn after Base finality (~8s Fast Transfer / ~15-19 min Standard Transfer).

3. **Attestation** — Circle issues an attestation (signed message) confirming the burn. Attestation timing is determined by **Base finality only** (~8s Fast / ~15-19 min Standard). X Layer destination processing is a separate transaction after attestation receipt.

4. **Mint on X Layer** — User (or relayer) calls `MessageTransmitterV2.receiveMessage(message, attestation)` on X Layer with the attestation. The CCTP contracts validate the message and mint USDC to the recipient address on X Layer. **Do not call `TokenMinterV2.mint()` directly** — `receiveMessage` is the correct CCTP V2 destination entry point.

5. **Deposit into TradingVault** — Since X Layer does not support Forwarding Service, the minted USDC does **not** auto-deposit into `TradingVault.sol`. The user (or an operator) must call `TradingVault.deposit()` on X Layer to move USDC from their wallet into the vault.

### Failure modes:
- **Insufficient allowance** — Fast Transfer subject to Circle's global allowance; may be unavailable during high volume
- **Insufficient base gas** — User needs ETH on Base for burn transaction gas, OP on X Layer for mint + vault deposit gas
- **Attestation expiry** — Attestations have a validity window; mint must occur before expiry
- **Reorg risk** — Standard Transfer waits for L1 finality (~15-19 min), minimizing reorg risk

---

## 6. Integration with TradingVault.sol

`TradingVault.sol` is already deployed on X Layer testnet (`0x747114F3Adc0338132A1E668128c2004f081cBaf`). The deposit flow would be:

1. User burns USDC on Base via CCTP `TokenMessengerV2.depositForBurn()`
2. Circle attests the burn (~8s Fast / ~15-19 min Standard)
3. User (or relayer) mints USDC on X Layer via `MessageTransmitterV2.receiveMessage(message, attestation)`
4. User calls `TradingVault.deposit(amount)` on X Layer, transferring minted USDC into the vault
5. Vault mints vault shares to the user, recorded on `TradeAuditTrail.sol` (already deployed on X Layer)

**No new vault deployment needed** — the existing X Layer vault receives the minted USDC directly.

---

## 7. Costs

| Cost Component | Base (source) | X Layer (destination) |
|---------------|---------------|----------------------|
| Burn tx gas | OP L2 gas (~$0.0001-0.001) | — |
| Mint tx gas | — | OP L2 gas (~$0.0001-0.001) |
| Vault deposit gas | — | OP L2 gas (~$0.0001-0.001) |
| CCTP fee | Deducted from transferred amount | — |

CCTP fees are deducted from the transferred USDC amount (not paid separately). Exact fee varies by chain and amount; check `TokenMessengerV2.getFee()` on-chain.

---

## 8. Risks & Limitations

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Forwarding Service unavailable on X Layer** | Medium | User/operator manually calls `TradingVault.deposit()` after mint; or build a relayer that watches for mint events and auto-deposits |
| **Standard Transfer finality ~15-19 min** | Medium | Base chain finality determines attestation wait; acceptable for vault deposits; not for instant trading |
| **Fast Transfer allowance limits** | Low | Use Standard Transfer as default; Fast Transfer as optional premium |
| **Circle attestation service downtime** | Low | Non-custodial — funds are burned on source; attestation delay doesn't lose funds, just delays mint |
| **USDC.e vs native USDC confusion** | Medium | X Layer has both Circle-issued native USDC (`0xB6CEceAB302E2E4948951eE7843FC24E92933061`) and bridged `USDC.e`. Only native USDC is CCTP-compatible. Verify address before integration. |

---

## 9. Implementation Path

### Phase A: Testnet validation (immediate, no capital at risk)
1. Deploy `TradingVault.sol` to X Layer testnet (already done at `0x747114F3Adc0338132A1E668128c2004f081cBaf`)
2. Deploy CCTP contracts on Base Sepolia and X Layer testnet (already deployed by Circle)
3. End-to-end test: burn USDC on Base Sepolia → mint on X Layer testnet → deposit into `TradingVault.sol`
4. Verify `TradeAuditTrail.sol` records the deposit decision

### Phase B: Mainnet deployment (post-G1, post-legal)
1. Deploy `TradingVault.sol` to X Layer mainnet
2. Configure `VAULT_MIN_DEPOSIT` / `VAULT_MAX_TVL` (immutable per deployment)
3. Set `ALLOW_LIVE` + `DRY_RUN=false` on satellite
4. Enable CCTP deposit path on depositor UI

---

## 10. Alternatives Considered

| Alternative | Assessment |
|-------------|------------|
| **Gateway (Circle unified balance)** | Instant (<500ms) but different balance model; not point-to-point. Overkill for vault deposits. |
| **Third-party bridge (e.g., Across, Orbiter)** | Faster but introduces third-party trust and liquidity pool risk. CCTP is non-custodial and Circle-issued. |
| **Direct OP bridge (Base → OP → X Layer)** | Two hops, higher cost, more failure points. CCTP is direct. |

CCTP is the recommended path: non-custodial, Circle-issued USDC, direct burn-and-mint, no liquidity pool risk.

---

## 11. Open Questions / Next Steps

1. **Vault deposit hook** — Does `TradingVault.sol.deposit()` accept USDC directly, or does it require a specific token? Verify contract interface before integration.
2. **Relayer for auto-deposit** — Since Forwarding Service is unavailable on X Layer, evaluate building a relayer that watches for CCTP mint events and auto-calls `TradingVault.deposit()`.
3. **Fee estimation** — Query `TokenMessengerV2.getFee()` on Base Sepolia to get exact CCTP fee for typical deposit amounts.
4. **Testnet E2E** — Execute a full Base Sepolia → X Layer testnet → TradingVault deposit cycle to validate end-to-end.

---

## Milestone Status

| Workstream | Status |
|------------|--------|
| CCTP feasibility and domain mapping | Complete — Base (domain 6) and X Layer (domain 37) both support CCTP V2 |
| Forwarding Service limitation | Verified — X Layer does not support Forwarding Service as destination; manual or relayer deposit required |
| Proposed transfer architecture | Feasible — Base burn → attestation → `MessageTransmitterV2.receiveMessage()` → `TradingVault.deposit()` |
| `TradingVault.deposit()` compatibility | Outstanding — verify vault accepts USDC directly |
| Fee and gas estimates | Outstanding — query `TokenMessengerV2.getFee()` on Base Sepolia |
| End-to-end testnet validation | Outstanding — Base Sepolia → X Layer testnet → TradingVault |
| Mainnet deployment | Blocked under Phase 9 gate |

**Boundary:** This is Phase 9 research only. No live capital, no key usage, no VPS changes. End-to-end testnet transactions and mainnet deployment remain separate from the current read-only work boundary.

---

## Sources

- Circle CCTP docs: https://developers.circle.com/cctp
- Supported chains & domains: https://developers.circle.com/cctp/concepts/supported-chains-and-domains
- Finality & block confirmations: https://developers.circle.com/cctp/concepts/finality-and-block-confirmations
- Contract addresses: https://developers.circle.com/cctp/references/contract-addresses
- USDC contract addresses: https://developers.circle.com/stablecoins/usdc-contract-addresses
- CCTP quickstart (Ethereum → Arc): https://developers.circle.com/cctp/quickstarts/transfer-usdc-ethereum-to-arc
