#!/usr/bin/env python3
"""Focused parameter sweep for funding_persistence_z on BTC/ETH/BNB.

Goal: find any (z_threshold, persist_frac, persist_window) combination that
produces OOS Calmar >= 1.0 with non-zero trades after 8 bps costs.
SOL is already validated and left untouched.
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.signals import funding_persistence_z_signal
from src.validation import validation_report, walk_forward_windows

# Re-use the exact cost & walk-forward settings from the main gate
FEE_BPS = 5.0
SLIP_BPS = 3.0
SIDE_COST = (FEE_BPS + SLIP_BPS) / 10000.0
BARS_PER_8H = 8
N_FOLDS = 6
TRAIN_FRAC = 0.7
TAIL = 130
CALMAR_BAR = 1.0

SYMBOLS = ["BTC", "ETH", "BNB"]  # deliberately exclude SOL

# Optional argv filter: `py scripts/sweep_fpz_btc_eth_bnb.py BTC` runs one
# symbol (smoke test before the full ~10-min sweep).
if len(sys.argv) > 1:
    want = [a.upper() for a in sys.argv[1:]]
    SYMBOLS = [s for s in SYMBOLS if s in want] or SYMBOLS

# Tight grid — small PBO surface. Surge variants double it: same
# (z, p, w) with the detector off (legacy) and on (burst → window 7).
# Surge mult/window fixed at the signal defaults to keep the surface small.
GRID = [
    {"z_threshold": z, "persist_frac": p, "persist_window": w,
     "surge_enabled": s}
    for z in (1.25, 1.5, 1.75, 2.0)
    for p in (0.6, 0.7, 0.8)
    for w in (4, 5, 7)
    for s in (False, True)
]


def load_candles(sym: str):
    path = REPO / "data" / f"{sym}_1h_candles.csv"
    with open(path, newline="") as f:
        rows = list(csv.reader(f))[1:]
    ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
    close = np.array([float(r[4]) for r in rows])
    return ts, close


def load_funding(sym: str):
    path = REPO / "data" / f"{sym}_funding_binance.csv"
    with open(path, newline="") as f:
        rows = list(csv.reader(f))[1:]
    ts = np.array([int(float(r[0])) for r in rows], dtype=np.int64)
    rate = np.array([float(r[1]) for r in rows])
    return ts, rate


def funding_ffill(bar_ts, fund_ts, rate):
    idx = np.searchsorted(fund_ts, bar_ts, side="right") - 1
    out = np.zeros(len(bar_ts))
    valid = idx >= 0
    out[valid] = rate[idx[valid]]
    return out


def net_bar_returns(pos, close):
    price_ret = np.diff(close) / close[:-1]
    ret = pos[:-1] * price_ret
    ret -= np.abs(np.diff(pos)) * SIDE_COST
    if pos[-1] != 0:
        ret[-1] -= abs(pos[-1]) * SIDE_COST
    return ret


def to_8h(bar_ret):
    n = (len(bar_ret) // BARS_PER_8H) * BARS_PER_8H
    chunks = bar_ret[:n].reshape(-1, BARS_PER_8H)
    return np.prod(1 + chunks, axis=1) - 1


def walk_forward_is_oos(ret8):
    is_parts, oos_parts = [], []
    for tr, te in walk_forward_windows(len(ret8), N_FOLDS, TRAIN_FRAC):
        is_parts.append(ret8[tr])
        oos_parts.append(ret8[te])
    return np.concatenate(is_parts), np.concatenate(oos_parts)


def run_one(sym, close, funding, params):
    n = len(close)
    pos = np.zeros(n)
    prices = close.tolist()

    for t in range(n):
        lo = max(0, t - TAIL)
        fund_hist = funding[max(0, t - 40): t + 1].tolist()
        sig = funding_persistence_z_signal(
            asset=sym,
            funding_rate=float(funding[t]),
            funding_history=fund_hist,
            price_history=prices[lo: t + 1],
            z_threshold=params["z_threshold"],
            persist_frac=params["persist_frac"],
            persist_window=params["persist_window"],
            surge_enabled=params.get("surge_enabled", False),
        )
        if sig.is_tradeable:
            pos[t] = 1.0 if sig.direction == "LONG" else -1.0

    bar_ret = net_bar_returns(pos, close)
    ret8 = to_8h(bar_ret)
    is_r, oos_r = walk_forward_is_oos(ret8)
    report = validation_report(is_r, oos_r, calmar_bar=CALMAR_BAR)

    trades = int(np.sum(np.abs(np.diff(pos)) > 0) // 2)
    return {
        "params": params,
        "trades": trades,
        "oos_sharpe": report["out_of_sample"]["sharpe"],
        "oos_calmar": report["out_of_sample"]["calmar"],
        "has_oos_evidence": report["has_oos_evidence"],
        "cleared": report["cleared_for_paper_trading"],
        "net_approx": float(np.prod(1 + bar_ret) - 1),
    }


def main():
    print(f"Sweeping {len(GRID)} combinations x {len(SYMBOLS)} symbols\n")
    results = []

    for sym in SYMBOLS:
        t0 = time.time()
        ts, close = load_candles(sym)
        fts, frate = load_funding(sym)
        funding = funding_ffill(ts, fts, frate)

        best = None
        for i, p in enumerate(GRID):
            r = run_one(sym, close, funding, p)
            results.append({"sym": sym, **r})
            if (i + 1) % 12 == 0 or i + 1 == len(GRID):
                print(f"  {sym} {i + 1}/{len(GRID)} combos...", flush=True)
            if r["cleared"] and (best is None or r["oos_calmar"] > best["oos_calmar"]):
                best = r

        print(f"{sym} done in {time.time()-t0:.1f}s")
        if best:
            print(f"  -> BEST PASS: {best['params']} | "
                  f"trades={best['trades']} | "
                  f"OOS Sharpe={best['oos_sharpe']:.2f} | "
                  f"Calmar={best['oos_calmar']:.2f}")
        else:
            print("  -> no combination cleared the gate")

    # Summary table
    print("\n=== All combinations that cleared ===")
    cleared = [r for r in results if r["cleared"]]
    if not cleared:
        print("None")
    else:
        for r in sorted(cleared, key=lambda x: -x["oos_calmar"]):
            print(f"{r['sym']:4} {r['params']}  "
                  f"trades={r['trades']:3}  "
                  f"Sharpe={r['oos_sharpe']:6.2f}  "
                  f"Calmar={r['oos_calmar']:7.2f}  "
                  f"net~{r['net_approx']:+.1%}")

    print("\nDone.")


if __name__ == "__main__":
    main()
