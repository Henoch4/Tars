# Datasets Needed + Free Sources

> All current Tarstrade datasets are free public endpoints — no keys, no paid APIs. Keep this doc for rebuilds.

---

## 1. For Live SOL (and BTC/ETH sweep) — Rule-Based `funding_persistence_z`

You already have these — just keep fetching:

| Dataset | File | Source (free, no auth) | How |
|---|---|---|---|
| **1h OHLCV** (BTC/ETH/SOL/BNB-USDT-SWAP) | `data/*_1h_candles.csv` — `ts,open,high,low,close,vol` | `https://www.okx.com /api/v5/market/history-candles?instId=SOL-USDT-SWAP&bar=1H&limit=100` | `python scripts/fetch_history.py` (20 req/2s, paginates 2y) |
| **Funding rate 8h** | `data/*_funding.csv` (OKX 3mo) + `data/*_funding_binance.csv` (2y proxy) — `ts,fundingRate` | OKX `/api/v5/public/funding-rate-history` + Binance `https://fapi.binance.com/fapi/v1/fundingRate?symbol=SOLUSDT&limit=1000` | Same script — Binance fills OKX 3mo cap, `funding_ffill()` uses most recent *settled* rate (no lookahead) |

**Validation uses these two only** (`close` + `funding`). SOL `funding_persistence_z` PASS (28 trades, Sharpe 2.20, Calmar 16.12) came from this.

**Keep fresh:** cron `python scripts/fetch_history.py` daily — adds ~3 funding points/day, keeps `funding_history` 21-length window valid.

---

## 2. For Paused ML Carry Pipeline (when resumed)

| Dataset | File | Source | How |
|---|---|---|---|
| **Carry dataset (built)** | `data/carry/dataset.csv` — 52,320 rows × 29 cols `sym,ts,fold,split,usable,f0..f20` + labels `y_sum_7d_bps / y_win / y_flip` | Built from 70 Binance perps (4y funding, 2y perp+spot 1h) via `scripts/fetch_carry_data.py` + `scripts/build_carry_dataset.py` (per-8h norm, 6×70/30 folds, 7-day embargo) | Already built 2026-08-23 — train with `python -m ml.pipeline --symbols BTC,ETH,SOL,BNB`. Colab T4 free 12h/session. Rebuild tail with `python scripts/fetch_carry_data.py --refresh` |

No new dataset needed — just train. Kill criterion to beat: zero-RMSE 26.1 bps, win rate 59.6% on 7d funding windows.

---

## 3. For Memecoin Book 2 (future, Archetype 2 — not funding)

Funding doesn't exist for memecoins (spot-only). You need **on-chain**:

| Dataset | Source (free tier) | Use per `EXPANSION_ROADMAP.md:52` |
|---|---|---|
| **Holder concentration, sniper wallets, LP locks, mint/freeze, age, honeypot** | Solana RPC `https://api.mainnet-beta.solana.com` + Helius 100k req/mo free, or Bitquery `https://ide.bitquery.io` (Pons on Robinhood Chain) | Scream filter: reject ~95% pre-buy |
| **Smart-money flows** | `smartmoney_signal` already plumbed + Bitquery | Whale Tracker >68% win wallets |
| **DEX price / liquidity / reserve drain** | Jupiter `https://price.jup.ag`, Raydium API, Dexscreener `https://api.dexscreener.com/latest/dex/pairs/solana/<mint>` | Spot leg `size/liquidity` slippage, rug detector |

Free until scale: Helius 100k/mo, Bitquery 10k points/mo, Dexscreener free.

---

## 4. You Do NOT Need (unless expanding)

- OI (`openInterest`) — not fetched live, not needed for `funding_persistence_z`
- Orderbook snapshots — only for surge arb (`yes+no<1` bored2boar style)
- LunarCrush sentiment — optional regime feature, not primary

---

## Quick Commands

```bash
# Refresh 2y history (all 4 majors)
python scripts/fetch_history.py --refresh

# Check dataset ready
python -c "import pathlib, csv; p=pathlib.Path('data/carry/dataset.csv'); print(p.stat().st_size); print(next(csv.reader(open(p)))[0:8])"

# Run ML when resumed
python -m ml.pipeline --symbols BTC,ETH,SOL,BNB
# or rule-based gate
python scripts/run_validation_gate.py
```

*Saved 2026-09-06 — all sources free, no keys.*
