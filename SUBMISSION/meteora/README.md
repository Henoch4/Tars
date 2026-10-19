# TARS — A Virtual Crypto Wall Street on Meteora DBC

**Track:** Best use of Meteora's Dynamic Bonding Curve (Colosseum Crypto World's Fair).
**Lane:** Equity/stock-pair launch mechanics + dev tooling (fee-schedule selection).
**Status:** Devnet-live, paper-verified. No mainnet claims.

## What TARS is (the 10-second version)

**A virtual crypto Wall Street.** A swarm of AI quant traders votes on
every trade. The guardrails they can't bypass: hard spending caps, a
stop-everything button, and every decision written to a public ledger
**before** the trade happens — so you can check the receipts yourself,
on-chain, without trusting us.

Think of it as a trading floor where the compliance department outranks
the traders, and every trade gets a receipt you can look up.

## What we built on Meteora's DBC

Memecoin launchpads run one curve for everything. **Equity-like assets
need the opposite** — and that's where most launches lose money:

- A stock that barely moves doesn't need the same fee schedule as a
  memecoin that 10x's in an hour. Charging stress-level fees on a calm
  asset means traders pay more to trade than the trade is worth.
- Our system picks the right fee schedule automatically: **calm markets
  get cheap linear fees, volatile markets get the exponential schedule
  that protects liquidity providers.** The switch is driven by market
  data, not a human flipping a switch.

### What that means for a launchpad builder

| Without TARS's selector | With TARS's selector |
|---|---|
| One curve fits all (usually exponential) | Calm assets get linear fees automatically |
| New equity tokens pay memecoin-level fees | Fee cost matches the asset's actual volatility |
| No way to prove which schedule fits | Backtest shows exactly where each schedule wins/loses |
| Manual fee tuning per launch | Regime-driven: the system adapts as the market shifts |

## The evidence (what runs on Meteora's stack)

| What | Proof |
|---|---|
| Live DBC pools for stock-paired assets (AAPLX/TSLAx/NVDAX) | Devnet pools with real swaps executed — addresses in the table below |
| Two fee schedules, both live | Calm: linear 120→100bps · Stress: exponential 900→100bps |
| The selector that picks between them | Tested (7 tests) — reads market volatility, picks the schedule |
| The cost math that proves which schedule fits | Reproducible backtest — including the honest finding that stress fees make short-term carry uninvestable |
| Every trade decision logged on-chain before execution | 880+ test suite, live on X Layer + Base Sepolia + Solana devnet |

**Devnet pool addresses** (verify on Solscan):
`CVrD4XycbRgMhWjr6tTQ6ebV41NGAG95cV8NccCvvPUU` ·
`84B8P25ec51c2szffJUEkkyMpUf81eqBFWHNH9pwDEsU` ·
`EebJJc253Rg5BJgiHCMkvGo2GzieZToSkDKvtw2FhqdH`

**Config addresses:** calm `3WDNBkpE…` · stress `7rhZYfkU…` (both in `docs/solana/dbc_configs.sol.json`)

## Why this matters beyond memecoins

The competitive landscape (Colosseum Copilot corpus, 5,400+ projects)
shows trading bots at crowdedness 270–323, but **nobody combines
multi-agent trading with non-overridable on-chain risk limits and a
regime-aware launch primitive.** The archive literature ("Agency by
Design", "Who Audits the Auditor?") says agent behavior must be
auditable and tamper-proof — our on-chain audit trail is that, applied
to a real trading system that already trades live.

Equity-paired DBC launches are the gap: thin, slow, fee-sensitive
assets that memecoin curves misprice. The regime selector is the
missing piece — and it generalizes to RWA pairs, commodity pairs, any
asset class where one-curve-fits-all leaves money on the table.

## Reproduce it

```bash
# Fee-schedule selection + cost math (offline)
python -m pytest tests/test_meme_venue_costs.py -q

# Full suite (880+ tests, everything the selector plugs into)
python -m pytest tests/ -q
```

## Honest limits

- Devnet pools, paper costs. No mainnet volume claim.
- The backtest found NO edge at tested horizons at high fee levels —
  submitted as evidence of method, not of profit. The finding *is* the
  product: the selector exists because the math says one schedule
  doesn't fit all.
- Mainnet is a funding decision, not a technical unknown: same
  primitive, new addresses, USDC quote instead of wSOL.

## Links

- DBC developer guide: https://docs.meteora.ag/developer-guides/dbc
- DAMM v2 guide: https://docs.meteora.ag/developer-guides/damm-v2
- Demo video: [record before submitting — script at docs/solana/VIDEO_SCRIPT.md]
