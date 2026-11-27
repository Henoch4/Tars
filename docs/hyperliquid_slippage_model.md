# Hyperliquid Orderbook Slippage Model — Research Report

**Date:** 2026-10-28  
**Status:** Implemented, awaiting quantitative validation  
**Script:** `scripts/hyperliquid_slippage.py`  
**Boundary:** Phase 9 research — capital deployment blocked until after Solana inbound  
**No keys, no state changes, no VPS impact.** Public read-only API only.

---

## 1. Model Design — Two Independent Estimates

The script provides two separate slippage estimates. They are intentionally
kept distinct because a live snapshot proves data retrieval, not execution
accuracy.

### A. Orderbook Execution Estimate (book-walk VWAP)

Walks the actual asks (buy) or bids (sell) from the live Hyperliquid
orderbook, filling the requested order size level-by-level. Computes
volume-weighted average fill price vs mid-price reference. Fees added
separately.

```
price_impact_bps = (vwap - mid) / mid * 1e4   (buy)
price_impact_bps = (mid - vwap) / mid * 1e4   (sell)
total_slippage_bps = price_impact_bps + taker_fee_bps
```

### B. Empirical Impact Estimate (square-root model)

```
impact_bps = k * sqrt(size_usd / depth_usd) * 1e4
```

Kept separate from book-walk. Coefficient `k` must be calibrated against
observed execution costs. **Not treated as verified.**

---

## 2. Fee Correction

Hyperliquid base taker fee schedule (from published docs):

| Market     | Base taker fee |
|------------|----------------|
| Perpetuals | 4.5 bps        |
| Spot       | 7 bps          |

Actual fees vary with account volume tier, staking discounts, and other
adjustments. The script defaults to **4.5 bps** (perpetuals base) but
accepts `--taker-fee-bps` for account-specific configuration. Do **not**
apply the perpetuals rate to spot markets.

---

## 3. Live Execution Results

Fetched live orderbooks from `https://api.hyperliquid.xyz/info` (public,
read-only, no key). Book-walk fills requested size level-by-level against
real prices and quantities.

### 3.1 Orderbook snapshots

| Coin | Best Bid | Best Ask | Mid Price |
|------|----------|----------|-----------|
| BTC  | 80,895.00 | 80,896.00 | 80,895.50 |
| ETH  | 2,355.50 | 2,355.60 | 2,355.55 |
| SOL  | 101.58 | 101.59 | 101.59 |

### 3.2 Book-walk execution estimates (taker fee = 4.5 bps)

| Coin | Side | Size | VWAP Fill | Price Impact | Total Slippage | Levels | Filled |
|------|------|------|-----------|--------------|----------------|--------|--------|
| BTC | buy | $1,000 | 80,896.00 | 0.06 bps | 4.56 bps | 1 | Yes |
| BTC | buy | $10,000 | 80,896.00 | 0.06 bps | 4.56 bps | 1 | Yes |
| BTC | buy | $50,000 | 80,896.00 | 0.06 bps | 4.56 bps | 1 | Yes |
| BTC | buy | $100,000 | 80,896.00 | 0.06 bps | 4.56 bps | 1 | Yes |
| BTC | sell | $1,000 | 80,895.00 | 0.06 bps | 4.56 bps | 1 | Yes |
| BTC | sell | $10,000 | 80,895.00 | 0.06 bps | 4.56 bps | 1 | Yes |
| BTC | sell | $50,000 | 80,893.51 | 0.25 bps | 4.75 bps | 3 | Yes |
| BTC | sell | $100,000 | 80,891.91 | 0.44 bps | 4.94 bps | 6 | Yes |
| ETH | buy | $1,000 | 2,355.60 | 0.21 bps | 4.71 bps | 1 | Yes |
| ETH | buy | $10,000 | 2,355.60 | 0.21 bps | 4.71 bps | 1 | Yes |
| ETH | buy | $50,000 | 2,355.60 | 0.21 bps | 4.71 bps | 1 | Yes |
| ETH | buy | $100,000 | 2,355.60 | 0.21 bps | 4.71 bps | 1 | Yes |
| ETH | sell | $1,000 | 2,355.50 | 0.21 bps | 4.71 bps | 1 | Yes |
| ETH | sell | $10,000 | 2,355.45 | 0.41 bps | 4.91 bps | 2 | Yes |
| ETH | sell | $50,000 | 2,355.32 | 0.99 bps | 5.49 bps | 4 | Yes |
| ETH | sell | $100,000 | 2,355.25 | 1.26 bps | 5.76 bps | 5 | Yes |
| SOL | buy | $1,000 | 101.59 | 0.49 bps | 4.99 bps | 1 | Yes |
| SOL | buy | $10,000 | 101.59 | 0.49 bps | 4.99 bps | 1 | Yes |
| SOL | buy | $50,000 | 101.59 | 0.49 bps | 4.99 bps | 1 | Yes |
| SOL | buy | $100,000 | 101.59 | 0.49 bps | 4.99 bps | 1 | Yes |
| SOL | sell | $1,000 | 101.58 | 0.49 bps | 4.99 bps | 1 | Yes |
| SOL | sell | $10,000 | 101.58 | 0.49 bps | 4.99 bps | 1 | Yes |
| SOL | sell | $50,000 | 101.57 | 1.36 bps | 5.86 bps | 3 | Yes |
| SOL | sell | $100,000 | 101.57 | 1.91 bps | 6.41 bps | 3 | Yes |

### 3.3 Key observations

1. **Taker fee dominates** — At 4.5 bps, fees are the majority of total slippage for all order sizes tested. Price impact is a small add-on for sizes up to $100k.

2. **Impact grows sub-linearly** — BTC sell goes from 0.06 bps ($1k) to 0.44 bps ($100k), a 7x size increase producing only ~7x impact. SOL sell goes from 0.49 bps ($10k) to 1.91 bps ($100k), again roughly linear in this range but with book-depth-dependent steps.

3. **All sizes filled** — The tested sizes up to $100k were fully filled against the live book with no insufficiency errors.

4. **Side asymmetry** — Sell impact is consistently higher than buy impact at the same size (e.g. BTC $100k: buy 0.06 bps vs sell 0.44 bps), reflecting the observed ask/bid depth asymmetry.

---

## 4. Script (`scripts/hyperliquid_slippage.py`)

### Functions

| Function | Purpose |
|----------|---------|
| `get_l2_book(coin)` | Fetch live L2 orderbook from Hyperliquid info API |
| `parse_l2_book(raw)` | Parse API response into OrderBook dataclass |
| `mid_price(book)` | Mid price = (best_bid + best_ask) / 2 |
| `walk_book_for_size(levels, side, size_usd)` | Walk book levels filling requested notional |
| `book_walk_execution(size_usd, side, book, taker_fee_bps)` | **Estimate A**: book-walk VWAP + fees |
| `impact_bps_sqrt(size_usd, depth_usd, k)` | **Estimate B**: square-root empirical model |
| `depth_usd_walk(levels, max_usd)` | Walk book accumulating USD depth |
| `compare_models(size_usd, side, book, taker_fee_bps, k)` | Side-by-side A vs B |
| `fetch_and_compare(coin, size_usd, side, taker_fee_bps, k)` | Live fetch + compare |

### Usage

```bash
# Live book-walk execution estimate
python scripts/hyperliquid_slippage.py --coin BTC --size-usd 10000 --side buy --fetch-live

# With custom taker fee (spot market)
python scripts/hyperliquid_slippage.py --coin BTC --size-usd 10000 --side buy --fetch-live --taker-fee-bps 7.0

# Square-root model with custom k
python scripts/hyperliquid_slippage.py --coin BTC --size-usd 100000 --side sell --fetch-live --k 0.3
```

### Example output (book-walk section)

```json
{
  "coin": "BTC",
  "side": "buy",
  "size_usd": 10000.0,
  "mid_price": 80895.5,
  "best_price": 80896.0,
  "vwap_fill_price": 80896.0,
  "filled_usd": 10000.0,
  "fully_filled": true,
  "price_impact_bps": 0.06,
  "taker_fee_bps": 4.5,
  "total_slippage_bps": 4.56,
  "levels_consumed": 1,
  "levels_detail": [...],
  "ts": 1787414400.0
}
```

---

## 5. Square-Root Model — Not Yet Calibrated

The `impact_bps_sqrt` function implements:

```
impact_bps = k * sqrt(size_usd / depth_usd) * 1e4
```

**Status:** Implemented, not calibrated. The `k` coefficient must be fit
against observed execution costs (realized fills). Current default `k=0.5`
is a placeholder.

To calibrate:
1. Collect realized fill data from Hyperliquid testnet/mainnet
2. For each fill, record: size, side, book depth at time of fill, actual slippage
3. Minimize `(predicted_impact - realized_impact)^2` across all fills to find `k`
4. Evaluate prediction error across symbols, order sizes, and market conditions

---

## 6. Milestone Status

| Component | Assessment |
|-----------|------------|
| Public L2 API integration | Verified — live data fetched successfully |
| Orderbook parsing | Implemented — bids/asks parsed into BookLevel dataclass |
| Book-walk execution estimate | Implemented — VWAP calculated from real levels |
| Square-root impact calibration | Outstanding — k not fitted to realized fills |
| Market-specific fee modeling | Implemented — 4.5 bps perp / 7 bps spot base, configurable |
| Historical validation against realized fills | Outstanding — no historical data source yet |
| Capital deployment | Blocked — Phase 9 boundary |

**Classification:** The script and live-data integration are complete. The
slippage model is **implemented but awaiting quantitative validation**.
Book-walk execution estimates are directly observable from live data; the
square-root impact coefficient `k` requires calibration against realized
fills before it can be used for execution cost prediction.

---

## 7. Reproducibility

```
python scripts/hyperliquid_slippage.py \
    --coin BTC \
    --size-usd 10000 \
    --side buy \
    --fetch-live \
    --taker-fee-bps 4.5 \
    --k 0.5
```

Outputs book-walk execution estimate (VWAP, price impact, total slippage)
and square-root empirical estimate side by side.

**Boundary:** Phase 9 research only. No live trading, no key usage, no VPS
changes. Window #4 (paper satellite) remains intact.
