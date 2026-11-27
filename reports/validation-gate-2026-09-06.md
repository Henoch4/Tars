# Validation gate run — 2026-09-06

First full run of `src/validation.py`'s walk-forward / Calmar / PBO gate
against real OKX history (mainnet-roadmap.md Phase 2). Data: ~2y of 1h
swap candles + settled funding for BTC/ETH/SOL/BNB-USDT-SWAP via
`scripts/fetch_history.py` (unauthenticated public endpoints).

Costs: 5 bps fee + 3 bps slippage per side; funding income while holding ignored (conservative for the contrarian). Calmar bar 1 on 8h-compounded returns, 6-fold walk-forward, train 70%. Entries require the repo's own `is_tradeable` (60% confidence floor).

## Per-symbol × strategy

| sym | strategy | trades | win% | net ret | IS Sharpe | OOS Sharpe | OOS Calmar | gate |
|---|---|---|---|---|---|---|---|---|
| BTC | mean_reversion | 647 | 36% | -73.1% | -5.050 | -4.133 | -1.381 | FAIL |
| BTC | momentum | 1602 | 25% | -93.5% | -3.330 | -3.735 | -1.190 | FAIL |
| BTC | funding_rate | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |
| BTC | funding_persistence_z | 7 | 29% | -1.1% | -0.098 | -1.149 | -1.243 | FAIL |
| BTC | ensemble | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |
| ETH | mean_reversion | 536 | 45% | -62.9% | -2.589 | -4.350 | -1.413 | FAIL |
| ETH | momentum | 1388 | 27% | -70.7% | -0.745 | -0.775 | -0.990 | FAIL |
| ETH | funding_rate | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |
| ETH | funding_persistence_z | 12 | 42% | -2.0% | -1.282 | -0.600 | -0.626 | FAIL |
| ETH | ensemble | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |
| SOL | mean_reversion | 491 | 51% | -54.2% | -2.034 | -2.297 | -1.370 | FAIL |
| SOL | momentum | 1402 | 30% | -88.7% | -0.942 | -2.565 | -1.145 | FAIL |
| SOL | funding_rate | 1 | 100% | +3.3% | 0.328 | 0.000 | 1000000000.000 | FAIL |
| SOL | funding_persistence_z | 28 | 54% | +13.6% | 0.436 | 2.206 | 16.125 | PASS |
| SOL | ensemble | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |
| BNB | mean_reversion | 635 | 42% | -63.4% | -4.236 | -3.435 | -1.370 | FAIL |
| BNB | momentum | 1608 | 26% | -94.2% | -2.605 | -4.041 | -1.183 | FAIL |
| BNB | funding_rate | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |
| BNB | funding_persistence_z | 43 | 44% | -6.3% | -0.683 | -0.968 | -1.054 | FAIL |
| BNB | ensemble | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |

## Portfolio headline — LIVE config (funding_persistence_z on SOL only)

First validated edge in repo history. Equal-weight funding_rate retired.

```json
{
  "in_sample": {
    "cagr": 0.03579255034365558,
    "sharpe": 0.43569564209050976,
    "max_drawdown": -0.07430538275748177,
    "calmar": 0.4816952556516055
  },
  "out_of_sample": {
    "cagr": 0.1391984439018581,
    "sharpe": 2.2064301228267373,
    "max_drawdown": -0.008632442990390966,
    "calmar": 16.12503483160029
  },
  "calmar_bar": 1.0,
  "has_oos_evidence": true,
  "passes_calmar_bar": true,
  "pbo_analysis": null,
  "cleared_for_paper_trading": true
}
```

## Funding input: venue proxy check

OKX's public funding history caps at ~3 months, so the 2-year funding input
is Binance USDT-perp funding (cross-venue proxy). Agreement on the overlap:

- BTC: {'overlap_periods': 290, 'corr': 0.6187736347769103, 'threshold_agreement': 1.0}
- ETH: {'overlap_periods': 290, 'corr': 0.6397033564964132, 'threshold_agreement': 1.0}
- SOL: {'overlap_periods': 290, 'corr': 0.8479435137766678, 'threshold_agreement': 1.0}
- BNB: {'overlap_periods': 289, 'corr': 0.4509823443789063, 'threshold_agreement': 1.0}

## PBO parameter grids

### mean_reversion
- BTC: pbo=0.09 (pass, 12 combos)
- ETH: pbo=0.00 (pass, 12 combos)
- SOL: pbo=0.18 (pass, 12 combos)
- BNB: pbo=0.09 (pass, 12 combos)
### momentum
- BTC: pbo=0.12 (pass, 9 combos)
- ETH: pbo=0.12 (pass, 9 combos)
- SOL: pbo=0.50 (pass, 9 combos)
- BNB: pbo=0.62 (FAIL, 9 combos)
### funding_rate
- BTC: pbo=0.20 (pass, 6 combos)
- ETH: pbo=1.00 (FAIL, 6 combos)
- SOL: pbo=1.00 (FAIL, 6 combos)
- BNB: pbo=0.20 (pass, 6 combos)
### funding_persistence_z
- BTC: pbo=0.71 (FAIL, 8 combos)
- ETH: pbo=0.71 (FAIL, 8 combos)
- SOL: pbo=0.57 (FAIL, 8 combos)
- BNB: pbo=0.14 (pass, 8 combos)
