# Carry backtest — verdict: costs eat the edge at these horizons

Run: `python scripts/backtest_carry.py [--borrow-bps 100] [--cost-bps 150]`
Date: 2026-09-20. Substrate: Yahoo underlying daily (AAPL/TSLA/NVDA, 251 bars).

## What was tested

The live signal rule (`stock_carry.tokenized_stock_carry_signal`: 50bps
carry floor, 70% confidence base) reduced to its economics: long/flat on
carry ≥ 50bps, daily PnL = carry/365 (price drift stripped — the
delta-neutral claim), turnover charged per flip. 60d in-sample, 30d
out-of-sample (most recent 30d).

## Assumptions (all stated, none hidden)

| Item | Value | Source / honesty note |
|---|---|---|
| Dividends | trailing-12m cash / spot, per day | Yahoo `events=div`, verified live |
| Borrow/lending | +100bps/yr | PROXY — locate data is Phase-C paid; flatters carry if true cost is higher |
| Funding | 0 | spot carry has no perp leg; DBC leg modeled via turnover fee |
| Turnover cost | 50 / 150 / 500bps per flip | 150 ≈ DBC linear avg (~110) + live venue spread (~60); 500 ≈ stress-regime bound; 50 ≈ optimistic floor |
| Basis timing | EXCLUDED | no token history exists publicly (xStocks live-only, pools days old); basis is measured live, not backtested |
| Short leg | excluded (long/flat only) | no spot short in the demo vault |

## Results (net total, bps)

| Cost/flip | AAPLx IS | AAPLx OOS | TSLAx IS | TSLAx OOS | NVDAx IS | NVDAx OOS |
|---|---|---|---|---|---|---|
| 50bps | -29.4 | -39.1 | -33.6 | -41.8 | -32.1 | -40.6 |
| 150bps | -129.6 | -139.2 | -133.8 | -141.9 | -132.2 | -140.7 |
| 500bps | -480.4 | -489.6 | -484.4 | -492.2 | -482.9 | -491.0 |

Day win rate 1.0 everywhere (carry days are all positive by construction —
no price risk modeled); max DD ≈ entry cost; 1 flip each (carry clears the
50bps floor every day on borrow alone). Sharpe is degenerate here (~zero
variance by construction) and is NOT the metric — judge on net bps and DD.

## Verdict: NO EDGE at these horizons/fees — and that is the finding

Carry earns ~150bps/yr ≈ 0.41bps/day. A 150bps entry takes ~365 days to
earn back. At 60/30-day horizons the strategy donates fees at every cost
level, in-sample AND out-of-sample, on all three assets, consistently.
The basis leg (excluded) and longer holds are the only paths to green:

- Break-even hold at 150bps cost: ~1 year. The 90/10 vault's 1-year note
  framing is therefore load-bearing, not decorative.
- Stress-regime fees (900bps schedule) make sub-year carry uninvestable;
  calm-regime linear fees are a precondition, which is exactly what the
  regime-tuned configs exist to select.
- If live venue spreads (currently ~60bps) persist, they are a second,
  separate return source the backtest cannot see — measured live, not here.

## What would change this verdict

1. Real borrow/locate data (tighter than the 100bps proxy either way).
2. Token history once DBC pools age (basis timing backtestable).
3. Lower all-in turnover (tighter pools, calmer regimes).
4. Longer horizon framing (the vault already does this).

Reproduce: one command above. No cherry-picking possible — same rule,
same costs, all assets, both windows.
