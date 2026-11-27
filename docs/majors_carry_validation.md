# Majors Carry Validation — Book 1

**Date:** 2026-10-28  
**Status:** Complete — offline historical research, read-only  
**Script:** `scripts/majors_carry_validation.py`  
**Results:** `reports/majors_carry_validation_20261028_022108.json`  
**Gate:** Research only — Window #4 (paper satellite) untouched, no keys used, no VPS changes

---

## Executive Summary

The delta-neutral spot-perp carry strategy was backtested against real OKX market
data for BTC, ETH, SOL, and BNB across ~2 years of history (Binance funding as
cross-venue proxy for OKX, ~3 months OKX direct). **The strategy does not have
edge after realistic costs.** All four symbols are net negative after fees,
slippage, and funding costs, and none beats the no-trade baseline (0 bps).

This confirms the earlier falsification finding from `build_carry_dataset.py`
(2026-08-23): in 2024-26 data, carry persistence does not clear costs.

---

## Step 1 — Historical Data Integrity

### Data sources

| Symbol | OKX funding (direct) | Binance funding (proxy) | OKX 1h candles |
|--------|---------------------|------------------------|----------------|
| BTC    | 290 rows (~3 months) | 1,991 rows (~2 years)  | 15,928 rows    |
| ETH    | 290 rows (~3 months) | 1,991 rows (~2 years)  | 15,928 rows    |
| SOL    | 290 rows (~3 months) | 1,991 rows (~2 years)  | 15,928 rows    |
| BNB    | 289 rows (~3 months) | 1,991 rows (~2 years)  | 15,929 rows    |

### Alignment quality

| Symbol | OKX-Binance overlap (rows) | OKX funding gaps (>8h) | Missing candles |
|--------|---------------------------|------------------------|-----------------|
| BTC    | 153                       | None                   | 0               |
| ETH    | 153                       | None                   | 0               |
| SOL    | 153                       | None                   | 0               |
| BNB    | 153                       | None                   | 0               |

- OKX funding covers ~3 months at 8h cadence (290 rows)
- Binance proxy covers ~2 years at 8h cadence (1,991 rows)
- OKX-Binance overlap: 153 timestamp-aligned observations per symbol
- No funding gaps >8h in OKX data; no missing candles for funded timestamps

### Data caveat

OKX direct funding history is capped at ~3 months. The 2-year backtest uses
Binance funding as a cross-venue proxy. OKX-Binance correlation on overlap
ranges from +0.41 (BNB) to +0.80 (SOL) — moderate directional alignment, not
proof of interchangeability for live execution.

---

## Step 2 — Cost Model

### Assumptions (stated explicitly, matches `src/agent.py` carry config)

| Cost component | Value | Applied to |
|----------------|-------|------------|
| Spot taker fee | 5 bps per side | Entry + exit, spot leg |
| Perp taker fee | 5 bps per side | Entry + exit, perp leg |
| Slippage | 3 bps per leg per side | Entry + exit, both legs |
| Funding cost | Per 8h period, rate-dependent | Collected/paid at each settlement |
| Round-trip cost | **32 bps** (both legs, both sides) | Every round-trip |

### Per-period break-even

```
ROUND_TRIP_COST_BPS = 2 × (5 + 5 + 2×3) = 32 bps
PERIOD_BREAK_EVEN_BPS = 32 × 2.0 / 21 = 3.05 bps per 8h period
```

The 7-day hold (21 periods) must collect at least 3.05 bps per period (64 bps
total over 7 days) to clear costs with a 2× margin. This matches
`carry_break_even_rate()` in `src/signals.py`.

**Control check:** Costs are charged on BOTH entry AND exit for BOTH legs —
not a generic cost subtracted from gross returns.

---

## Step 3 — Delta-Neutral Spot-Perp Package Validation

### Strategy logic (no look-ahead)

At each funding settlement timestamp t:
- Funding rate `f` known at time t (trailing, from the settlement at t)
- If `f > +threshold`: SHORT perp + LONG spot (collect funding when f > 0)
- If `f < -threshold`: LONG perp + SHORT spot (collect funding when f < 0)
- Else: FLAT (no position)

Hold for `hold_periods` funding periods (default 21 × 8h = 7 days), then close.
Funding collected is based on the rate known at open time t — no look-ahead into
future funding rates.

### Position sizing

$10,000 notional per leg (research normalization). Delta-neutral: long spot +
short perp (or vice versa), so directional price exposure offsets. Basis change
(spot-perp spread movement) is the residual risk.

### Per-cycle PnL decomposition

```
net_pnl_bps = funding_collected_bps - total_cost_bps - basis_change_bps
```

where:
- `funding_collected_bps` = rate × hold_periods × 1e4 (direction-adjusted)
- `total_cost_bps` = entry (fee+slip both legs) + exit (fee+slip both legs) = 32 bps
- `basis_change_bps` = spot_return - perp_return (delta-neutral residual)

---

## Step 4 — Out-of-Sample Validation (Walk-Forward)

Purged walk-forward with 6 folds, 70/30 train/test split, 7-day embargo on
train tail to prevent label straddle. Threshold grid [0, 0.5, 1, 2, 5, 10 bps]
searched on train, best threshold applied to test.

| Symbol | Funded cycles | Folds positive (test) | Mean test net (bps) | All folds positive |
|--------|--------------|----------------------|---------------------|-------------------|
| BTC    | 91           | 0/4                  | +0.00               | No                |
| ETH    | 91           | 0/4                  | +0.00               | No                |
| SOL    | 91           | 1/4                  | +6.62               | No                |
| BNB    | 40           | 0/2                  | -61.88              | No                |

**No fold configuration produces a consistently positive out-of-sample net.** The
best single fold (SOL, fold with +6.62 bps test net) is not reproducible across
folds — classic parameter-overfitting signature.

---

## Step 5 — Robustness Report

### Base case results (all symbols, 7-day hold, 32 bps round-trip cost)

| Symbol | Funded cycles | Win rate | Net bps | Max DD bps | Calmar | Beats no-trade |
|--------|--------------|----------|---------|------------|--------|----------------|
| BTC    | 91           | 2.2%     | -1,843  | ~1,843     | -1.0   | No             |
| ETH    | 91           | 5.5%     | -1,721  | ~1,778     | -0.97  | No             |
| SOL    | 91           | 8.8%     | -1,569  | ~1,569     | -1.0   | No             |
| BNB    | 40           | 12.5%    | -514    | ~565       | -0.91  | No             |

### Robustness to higher costs

| Symbol | Base (1×) | Cost ×1.5 | Cost ×2.0 | Cost ×3.0 | Cost ×5.0 |
|--------|-----------|-----------|-----------|-----------|-----------|
| BTC    | -1,843    | -3,299    | -4,755    | -7,667    | -13,491   |
| ETH    | -1,721    | -3,177    | -4,633    | -7,545    | -13,369   |
| SOL    | -1,569    | -3,025    | -4,481    | -7,393    | -13,217   |
| BNB    | -514      | -1,154    | -1,794    | -3,074    | -5,634    |

All scenarios worsen linearly with costs — as expected when costs dominate
funding income.

### Drawdown profile

Max drawdown approximately equals total net loss across all symbols — the
equity curve declines monotonically with no meaningful recovery periods. Calmar
ratios are negative (−0.91 to −1.0), confirming the strategy loses money on a
risk-adjusted basis.

---

## Conclusion: Carry Does Not Clear Costs

**Primary finding:** The delta-neutral spot-perp carry strategy has **no
demonstrated edge** after realistic costs across BTC, ETH, SOL, and BNB over
~2 years of history.

| Metric | Result |
|--------|--------|
| Net PnL (all symbols) | Negative (−514 to −1,843 bps) |
| Best symbol (BNB) | −513.56 bps |
| Win rate (best symbol) | 12.5% |
| Walk-forward folds positive | 0/2 to 1/4 |
| Beats no-trade baseline | No (all symbols) |
| Robust to higher costs | No (worsens linearly) |

### Why

Funding rates in 2024-26 data are too small and too short-lived to amortize
the 32 bps round-trip cost (entry + exit, both legs, fee + slippage). Even at
the 7-day holding period (21 funding periods), the typical per-period funding
rate (a few bps) multiplied by 21 periods (~60-70 bps gross) barely clears
costs, and most periods have rates near zero or negative (wrong direction for
the position).

This confirms the `build_carry_dataset.py` finding (2026-08-23): *"in 2024-26
data a 72h hold NEVER clears 2x a 35 bps round trip (max observed persistence
~7 bps/8h => ~65 bps/72h < 70 bps bar)."*

### Recommendation

**Do not deploy delta-neutral carry as a live strategy.** The funding-path
model should remain a research backlog item until:
1. A venue with structurally higher funding rates (or lower costs) is identified,
2. OR the cost model improves materially (lower fees/slippage, lower EV margin),
3. OR a different holding period / entry-exit rule demonstrates edge in
   out-of-sample testing.

Injective remains on the research backlog — it requires a historical mainnet
funding dataset before any proxy validation can proceed. Binance remains the
OKX funding proxy for offline research only, subject to the correlation caveats
noted above.

---

## Reproducibility

```
python scripts/majors_carry_validation.py \
    --symbols BTC,ETH,SOL,BNB \
    --years 2 \
    --hold-periods 21 \
    --min-rate-bps 0.0
```

Inputs:
- `data/{SYM}_funding.csv` — OKX funding-rate-history (~3 months)
- `data/{SYM}_funding_binance.csv` — Binance fapi fundingRate (~2 years proxy)
- `data/{SYM}_1h_candles.csv` — OKX 1h candles

Outputs:
- `reports/majors_carry_validation_*.json` — machine-readable results
- `docs/majors_carry_validation.md` — this report

**Boundary:** This is historical research and offline validation. Window #4
remains intact. No live capital, no VPS changes, no satellite modifications.
