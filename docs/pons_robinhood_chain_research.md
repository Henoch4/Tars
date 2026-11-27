# Robinhood Chain / PONS Research — Verification Status

**Date:** 2026-10-28  
**Status:** Research — offline, read-only  
**Boundary:** Window #4 (paper satellite) untouched. This is pure market research for Book 2 memecoin satellite evaluation.  
**No keys, no state changes, no VPS impact.** All data from DexScreener public API and Robinhood Chain documentation.

---

## 1. Robinhood Chain Network Configuration — Verified

Robinhood Chain is confirmed as a live, public EVM-compatible Layer 2 blockchain:

| Attribute | Value | Source |
|-----------|-------|--------|
| Chain ID | 4663 (mainnet), 46630 (testnet) | docs.robinhood.com/chain/connecting |
| Native gas token | ETH | docs.robinhood.com/chain/ |
| Architecture | Permissionless Layer 2, built on Arbitrum Dedicated Blockchains | docs.robinhood.com/chain/ |
| Block time | ~100ms | robinhood.com/us/en/crypto/chain/ |
| Public RPC | `https://rpc.mainnet.chain.robinhood.com` | docs.robinhood.com/chain/connecting |
| Block explorer | robinhoodchain.blockscout.com | docs.robinhood.com/chain/connecting |
| DEX | Uniswap | docs.robinhood.com/chain/ |
| Perps | Lighter, Arcus | docs.robinhood.com/chain/ |
| Stablecoin | USDG (Paxos) | docs.robinhood.com/chain/ |
| Lending | Morpho | docs.robinhood.com/chain/ |

**Architecture terminology:** Per Robinhood's official documentation, Robinhood Chain is an Arbitrum Layer 2 (not L3). The earlier description as "Arbitrum Orbit L3" was incorrect and has been corrected.

**Ecosystem partners:** Alchemy (RPC/AA infrastructure), LayerZero (cross-chain bridge), Chainlink (oracles), Fireblocks/BitGo (custody), Allium (analytics), Coingecko (token tracking), Uniswap (public DEX), Rialto/Arcus (perps), Morpho (lending), Paxos USDG (stablecoin), Zerion (wallet data), TRM Labs (compliance/risk).

---

## 2. PONS Token on Robinhood Chain — Live Data Verified

PONS is confirmed as a live token on Robinhood Chain:

**Token:** Pons (PONS)  
**Contract:** `0x39dBED3a2bd333467115dE45665cC57F813C4571`  
**Chain:** Robinhood Chain (dexscreener chainId: `robinhood`)  
**Pairs found:** 30 (all verified on Robinhood Chain chainId)

### Aggregate Metrics (all 30 Robinhood Chain PONS pairs)

| Metric | Value |
|--------|-------|
| Total 24h volume | $15,370,969.76 |
| Total liquidity | $10,178,375.75 |
| Price | ~$0.286 |
| Price change 24h | +19% to +20% across pairs |
| Price change 6h | +12% to +13% across pairs |

**Top pairs by 24h volume:**

| Pair | Price | Vol 24h | Liq | Price chg 24h | Buys 24h | Sells 24h |
|------|-------|---------|-----|---------------|----------|-----------|
| PONS/USDG | $0.2859 | $8,066,065.74 | $2,419,017.08 | +19.23% | 8,376 | 7,860 |
| PONS/WETH | $0.2859 | $927,184.10 | $1,546,474.42 | +19.31% | 2,092 | 1,691 |
| PONS/USDG | $0.2864 | $1,270,315.20 | $1,303,776.87 | +19.61% | 2,045 | 1,654 |
| PONS/USDG | $0.2863 | $1,388,183.44 | $464,373.12 | +19.49% | 1,887 | 1,680 |
| PONS/WETH | $0.2857 | $398,498.67 | $2,473,630.36 | +18.70% | 1,196 | 509 |

### Data Provenance

| Attribute | Value |
|-----------|-------|
| Retrieval timestamp | 2026-10-28T03:00:31.893416+00:00 |
| Token address | `0x39dBED3a2bd333467115dE45665cC57F813C4571` |
| Chain ID | `robinhood` (DexScreener) |
| Unique pair count | 30 |
| Source | DexScreener public API (`/latest/dex/tokens/{mint}`) |
| Chain filter applied | Yes (chainId == "robinhood") |
| Duplicate handling | Yes (by pair address) |
| Base token verification | Yes (matches PONS contract address) |

**Scripts:** `scripts/pons_robinhood_analysis.py`, `scripts/dexscreener_pons.py`  
**Raw output:** Saved to Temp (see script output for path)

---

## 3. Claims vs. Verified Reality

### Claims That CANNOT Be Verified from Public Data

| Claim from `EXPANSION_ROADMAP.md` (line 78) | Status | Reason |
|--------------------------------------------|--------|--------|
| "Pons launchpad $4.89M daily fees vs pump.fun $1.72M" | ❌ Cannot verify | DexScreener shows trading volume ($15.37M total across PONS pairs), not fee revenue. Fee data is not public. |
| "22,581 tokens/day" | ❌ Cannot verify | DexScreener does not expose token deployment counts or launchpad metrics. |
| "1.10% graduation (1,362/207,893)" | ❌ Cannot verify | DexScreener does not track graduation rates or token lifecycle. |
| "Pons rug rate 98.9%" | ❌ Cannot verify | DexScreener does not track rug events or token failure rates over time. |
| "Gas free until late Sep" | ❌ Cannot verify | Gas pricing data is not available from DexScreener or Robinhood Chain docs. |
| "24M+ funded accounts" | ❌ Cannot verify | User account counts are not on-chain data visible via DexScreener. |

### Claims That ARE Verified

| Claim | Status | Evidence |
|-------|--------|----------|
| Robinhood Chain exists as Arbitrum Layer 2 | ✅ | docs.robinhood.com/chain/ — Chain ID 4663, ETH gas, Arbitrum Dedicated Blockchains |
| PONS token trades on Robinhood Chain | ✅ | DexScreener: 30 pairs, base token `0x39dBED3a2bd333467115dE45665cC57F813C4571` |
| PONS has significant trading volume | ✅ | $15.37M total 24h volume across all pairs |
| PONS/USDG is the primary pair | ✅ | $8.07M 24h volume, $2.42M liquidity |
| PONS price is trending up | ✅ | +19% h24, +12% h6 across major pairs |
| High transaction count | ✅ | Up to 8,376 buys / 7,860 sells in 24h on PONS/USDG |

---

## 4. Key Differences from Pump.fun Model

The `EXPANSION_ROADMAP.md` describes Pons as a "bonding-curve launchpad" similar to Pump.fun. However, DexScreener data shows:

- **PONS trades as a standard token** on Robinhood Chain DEXes (Uniswap), not through a bonding-curve mechanism visible on DexScreener
- **High liquidity relative to volume**: $10.18M liquidity vs $15.37M 24h volume (66% liquidity-to-volume ratio), suggesting established market making rather than a bonding-curve launch
- **Price stability**: Prices are tightly clustered around $0.286 across all 30 pairs, with consistent +19% h24 / +12% h6 gains — this looks like an established token with consistent upward pressure, not a newly launched bonding-curve token experiencing pump-and-dump cycles

## 5. Implications for Book 2 Memecoin Satellite

The Kimchi lesson (dethective, 2026-10-24) showed that memecoin satellite success depends on:
1. **Entry timing** — not buying into orchestrated distributions
2. **Funding ancestry** — split buys across wallets are invisible to holder maps but visible in funding graphs
3. **Run position** — arriving before vs. after the price move

For Robinhood Chain / Pons specifically:

| Factor | Assessment |
|--------|------------|
| **Liquidity depth** | $10.18M total across all pairs — reasonable for small-size entries ($25-100) |
| **Volume** | $15.37M 24h — active but not pump.fun scale |
| **Price trend** | Consistently +19% h24 — suggests strong upward momentum, could indicate late entry risk |
| **Buy/sell ratio** | Generally positive (e.g., 8,376 buys / 7,860 sells on PONS/USDG) |
| **Custodial risk** | Robinhood Chain is Robinhood-operated; "no on-chain audit trail for executions" per EXPANSION_ROADMAP.md — would need API-level audit logs |
| **KYC/AML** | Robinhood is US-person KYC/AML — complicates anonymous satellite operation |

## 6. Recommendation

**Robinhood Chain / Pons is a viable but unverified venue for Book 2 memecoin satellite research.**

**Confirmed:**
- PONS token exists and trades on Robinhood Chain with real liquidity ($10.18M) and volume ($15.37M 24h)
- Robinhood Chain is a live Arbitrum Layer 2 with EVM compatibility
- DexScreener provides real-time pricing and volume data

**Not verified (from EXPANSION_ROADMAP.md claims):**
- $4.89M daily fees (DexScreener shows volume, not fees)
- 22,581 tokens/day deployment rate
- 1.10% graduation rate
- 98.9% rug rate
- Gas-free until late September

**Next steps if pursuing Robinhood Chain:**
1. Query Robinhood Chain blockscout (robinhoodchain.blockscout.com) for token deployment counts to verify "22,581 tokens/day"
2. Query blockscout for contract creation events to estimate graduation/rug rates
3. Check if Pons has a bonding-curve mechanism or is a standard tradable token
4. Verify gas costs on Robinhood Chain mainnet
5. Evaluate whether the +19% h24 trend represents sustainable growth or pump-and-dump risk

**Important caveat:** PONS token trading activity does not establish Pons launchpad activity. The volume and liquidity of PONS trading pairs describe activity in those pools — they do not, by themselves, establish how many tokens the launchpad creates, how many graduate, or how much revenue its protocol earns. An independent on-chain analysis would be needed to investigate launchpad activity and token lifecycle outcomes.

**Positive price movement is not proof of lasting market strength.** The +19% h24 trend across all pairs could reflect short-term momentum rather than sustainable demand.

---

## Sources

- Robinhood Chain docs: https://docs.robinhood.com/chain/
- Robinhood Chain connecting: https://docs.robinhood.com/chain/connecting
- Robinhood crypto: https://www.robinhood.com/us/en/crypto/
- DexScreener API docs: https://docs.dexscreener.com/
- PONS launchpad docs: https://docs.ponsfamily.com
- Bitquery Robinhood/Pons docs: https://docs.bitquery.io/docs/blockchain/robinhood/
