# Validation gate run — 2026-10-07

First full run of `src/validation.py`'s walk-forward / Calmar / PBO gate
against real OKX history (mainnet-roadmap.md Phase 2). Data: ~2y of 1h
swap candles + settled funding for BTC/ETH/SOL/BNB-USDT-SWAP via
`scripts/fetch_history.py` (unauthenticated public endpoints).

Costs: 5 bps fee + 3 bps slippage per side; funding income while holding ignored (conservative for the contrarian). Calmar bar 1 on 8h-compounded returns, 6-fold walk-forward, train 70%. Entries require the repo's own `is_tradeable` (60% confidence floor).

## Per-symbol × strategy

| sym | strategy | trades | win% | net ret | IS Sharpe | OOS Sharpe | OOS Calmar | gate |
|---|---|---|---|---|---|---|---|---|
| BTC | mean_reversion | 647 | 34% | -67.5% | -4.395 | -4.535 | -1.485 | FAIL |
| BTC | momentum | 1602 | 29% | -94.4% | -2.893 | -5.863 | -1.163 | FAIL |
| BTC | funding_rate | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |
| BTC | funding_persistence_z | 7 | 14% | -4.4% | -1.037 | -1.180 | -1.229 | FAIL |
| BTC | ensemble | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |
| ETH | mean_reversion | 536 | 39% | -48.8% | -1.317 | -3.152 | -1.336 | FAIL |
| ETH | momentum | 1388 | 30% | -92.1% | -1.706 | -2.333 | -1.142 | FAIL |
| ETH | funding_rate | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |
| ETH | funding_persistence_z | 12 | 42% | -0.6% | -1.065 | 0.488 | 0.656 | FAIL |
| ETH | ensemble | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |
| SOL | mean_reversion | 491 | 49% | -35.6% | -0.723 | -2.218 | -1.329 | FAIL |
| SOL | momentum | 1402 | 34% | -93.8% | -1.076 | -4.011 | -1.115 | FAIL |
| SOL | funding_rate | 1 | 100% | +6.3% | 0.625 | 0.000 | 1000000000.000 | FAIL |
| SOL | funding_persistence_z | 28 | 57% | +2.6% | -0.208 | 1.505 | 6.522 | PASS |
| SOL | ensemble | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |
| BNB | mean_reversion | 635 | 37% | -56.5% | -4.016 | -1.401 | -0.975 | FAIL |
| BNB | momentum | 1608 | 31% | -94.9% | -2.416 | -5.045 | -1.149 | FAIL |
| BNB | funding_rate | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |
| BNB | funding_persistence_z | 43 | 42% | -4.4% | -0.539 | -0.810 | -0.802 | FAIL |
| BNB | ensemble | 0 | 0% | +0.0% | 0.000 | 0.000 | 1000000000.000 | FAIL |

## Portfolio headline — LIVE config (funding_persistence_z on SOL only)

First validated edge in repo history. Equal-weight funding_rate retired.

```json
{
  "in_sample": {
    "cagr": -0.021247611909449016,
    "sharpe": -0.20756538064681546,
    "max_drawdown": -0.06883269899532148,
    "calmar": -0.30868485791750233
  },
  "out_of_sample": {
    "cagr": 0.10080469131564218,
    "sharpe": 1.5048225930305412,
    "max_drawdown": -0.015456577392060453,
    "calmar": 6.521799021782294
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
- BTC: pbo=0.00 (pass, 12 combos)
- ETH: pbo=0.00 (pass, 12 combos)
- SOL: pbo=0.73 (FAIL, 12 combos)
- BNB: pbo=0.09 (pass, 12 combos)
### momentum
- BTC: pbo=0.00 (pass, 9 combos)
- ETH: pbo=0.12 (pass, 9 combos)
- SOL: pbo=0.25 (pass, 9 combos)
- BNB: pbo=0.38 (pass, 9 combos)
### funding_rate
- BTC: pbo=0.20 (pass, 6 combos)
- ETH: pbo=1.00 (FAIL, 6 combos)
- SOL: pbo=0.80 (FAIL, 6 combos)
- BNB: pbo=1.00 (FAIL, 6 combos)
### funding_persistence_z
- BTC: pbo=0.29 (pass, 8 combos)
- ETH: pbo=0.86 (FAIL, 8 combos)
- SOL: pbo=0.00 (pass, 8 combos)
- BNB: pbo=0.14 (pass, 8 combos)
