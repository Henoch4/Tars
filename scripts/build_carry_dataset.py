#!/usr/bin/env python3
"""Build the leakage-safe training dataset for the funding-path / carry-timing
model (ML_ROADMAP_REVISED.md 2026-08-23 target #1 — workplan step 2).

Input : data/carry/{SYM}_funding.csv, {SYM}_perp_1h.csv, {SYM}_spot_1h.csv
        (scripts/fetch_carry_data.py)
Output: data/carry/dataset.csv — one row per (symbol, funding settlement):

  decision grid   every funding settlement (matches live cadence). Rates are
                  normalized to per-8h equivalents first: some Binance
                  symbols settle every 4h, and without normalization their
                  lags/labels are not comparable with 8h symbols.
  features        strictly trailing: funding lags/rollings/z-score,
                  perp-spot basis (bps) + its 24h mean/std, realized vol
                  (24h/72h) and returns (24h/72h) from perp 1h bars,
                  cross-asset mean funding + BTC funding z
  labels          y_sum_72h_bps : cumulative funding over the next 72h of
                              wall time (the persistence-scale number)
                  y_sum_7d_bps  : cumulative funding over the next 7 DAYS —
                              THE EV label. Empirical finding 2026-08-23: in
                              2024-26 data a 72h hold NEVER clears 2x a
                              35 bps round trip (max observed persistence
                              ~7 bps/8h => ~65 bps/72h < 70 bps bar), so the
                              trade decision must be judged on the natural
                              holding period, where costs amortize.
                  y_win         : y_sum_7d_bps > EV_MARGIN * COST_BPS
                  y_flip        : funding goes negative within 7d (exit-
                              timing ground truth)
  folds           purged walk-forward: the repo's walk_forward_windows over
                  the sorted ACTUAL row timestamps (not the raw funding grid
                  — funding was fetched 4y deep but candles cover 2y, and
                  folds must contain rows on both sides), 6 folds x (70/30),
                  with a 7-day EMBARGO dropped from each fold's train tail
                  so no forward label can straddle a split.
                  Columns: fold, split, usable (False = embargoed train row)

QA printed at build time: row counts, label rates, embargo assertion, and
the persistence-baseline RMSE any model must beat.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.validation import walk_forward_windows  # noqa: E402

CARRY = REPO / "data" / "carry"
COST_BPS = 35.0       # round-trip both legs, taker+slippage assumption
EV_MARGIN = 2.0       # y_win: 7d carry must clear 2x costs
H72_MS = 72 * 3600 * 1000
H7D_MS = 7 * 24 * 3600 * 1000
EMBARGO_MS = H7D_MS   # longest label horizon defines the embargo
N_FOLDS, TRAIN_FRAC = 6, 0.7

# New causal-input features (add to FEATURES list; premium-derived fields
# are NaN since /futures/data/premiumIndexHistory is rate-banned; LightGBM
# handles NaN natively — covers delisted tail rows)
NEW_CAUSAL_FEATURES = [
    "spell_age",           # time since last funding sign change (bars)
    "pin_state",          # 1 if funding rate at cap/floor, else 0
    "funding_rank_cross", # percentile rank of funding within cross-section
    "btc_funding_regime", # 1 if BTC funding > median, else 0
    "premium_zscore",     # NaN (premium index rate-banned); LightGBM NaN-safe
    "premium_residual",   # NaN (premium index rate-banned); LightGBM NaN-safe
]
FEATURES = [
    "f0", "f1", "f2", "f_del", "f_mean3d", "f_std3d", "f_mean7d", "f_std7d",
    "f_mean30d", "f_z30d", "f_hot7d",
    "basis_bps", "basis_mean24h", "basis_std24h",
    "vol_24h", "vol_72h", "ret_24h", "ret_72h",
    "x_mean_f", "x_btc_z",
] + NEW_CAUSAL_FEATURES


def load_csv(path: Path, ncols: int):
    rows = list(csv.reader(open(path, newline="")))[1:]
    return (np.array([int(r[0]) for r in rows], dtype=np.int64),
            np.array([[float(x) for x in r[1:1 + ncols]] for r in rows]))


def normalize_to_8h(ts: np.ndarray, rate: np.ndarray) -> np.ndarray:
    """Scale per-period rates to per-8h equivalents (4h cadence x2, etc.)."""
    if len(ts) < 3:
        return rate
    cadence_ms = float(np.median(np.diff(ts)))
    return rate * (8 * 3600 * 1000 / cadence_ms)


def trailing_stats(x: np.ndarray, win: int) -> tuple[np.ndarray, np.ndarray]:
    """Rolling mean/std over the PREVIOUS win values (exclusive of current)."""
    n = len(x)
    mean = np.full(n, np.nan)
    std = np.full(n, np.nan)
    c = np.concatenate([[0.0], np.cumsum(x)])
    c2 = np.concatenate([[0.0], np.cumsum(x * x)])
    for i in range(win, n):
        s = c[i] - c[i - win]
        s2 = c2[i] - c2[i - win]
        mean[i] = s / win
        std[i] = np.sqrt(max(s2 / win - (s / win) ** 2, 0.0))
    return mean, std


def build_symbol(sym: str, btc_z_by_ts: dict, xmean_by_ts: dict):
    ts_f, f = load_csv(CARRY / f"{sym}_funding.csv", 1)
    rate = normalize_to_8h(ts_f, f[:, 0])
    ts_p, p = load_csv(CARRY / f"{sym}_perp_1h.csv", 5)
    ts_s, s = load_csv(CARRY / f"{sym}_spot_1h.csv", 5)
    n = len(ts_f)

    m3, s3 = trailing_stats(rate, 9)     # ~3d at 8h cadence
    m7, s7 = trailing_stats(rate, 21)    # ~7d
    m30, s30 = trailing_stats(rate, 90)  # ~30d
    z30 = (rate - m30) / np.where(s30 > 1e-12, s30, np.nan)
    hot7 = np.array([np.sum(rate[max(0, i - 21):i] >= 5.0)  # >=5 bps/8h-equiv
                     for i in range(n)], dtype=float)

    # candle-aligned trailing features, mapped to each decision ts
    prets = np.diff(np.log(p[:, 3]))                  # perp close rets
    vol24 = trailing_stats(prets, 24)[1]
    vol72 = trailing_stats(prets, 72)[1]
    c = np.concatenate([[0.0], np.cumsum(prets)])
    ret24 = np.array([c[i] - c[i - 24] if i >= 24 else np.nan
                      for i in range(len(prets) + 1)])
    ret72 = np.array([c[i] - c[i - 72] if i >= 72 else np.nan
                      for i in range(len(prets) + 1)])
    # align spot closes onto the perp candle grid BY TIMESTAMP — index-wise
    # alignment would silently skew basis if either series has a gap
    sc = s[np.clip(np.searchsorted(ts_s, ts_p, side="right") - 1, 0, None), 3]
    basis = (p[:, 3] - sc) / sc * 1e4                  # bps, per perp candle
    bmean, bstd = trailing_stats(basis, 24)

    rows = []
    # ----- compute causal features per-row (before the row loop) -----
    # spell_age: bars since last funding sign change; f0 is rate column index 0
    f0_sign = np.sign(f[:, 0])
    spell = np.full(n, np.nan, dtype=float)
    last_crossing = -90
    for i in range(n):
        if f0_sign[i] != 0 and i > 0 and f0_sign[i] != f0_sign[i-1]:
            last_crossing = i
        spell[i] = i - last_crossing if last_crossing >= 0 else np.nan
    # pin_state: 1 if funding rate at cap or floor (|rate| > threshold)
    PIN_THRESHOLD = 5.0 / 1e4  # 5 bps in per-8h equivalent
    pin = np.where(np.abs(rate) > PIN_THRESHOLD, 1.0, 0.0).astype(float)
    # funding_rank_cross: percentile rank of current rate within the cross-section
    # at each timestamp (only available on rows that share the same ts)
    # We'll compute this as a simpler per-symbol rank over the whole history
    rate_rank = np.full(n, np.nan, dtype=float)
    for i in range(n):
        # count how many earlier rates are <= current rate
        if not np.isnan(rate[i]):
            rank = np.sum(rate[:i+1] <= rate[i]) / max(1, i+1) * 100
            rate_rank[i] = rank
    # btc_funding_regime: 1 if BTC funding > median, else 0
    # (btc_z_by_ts already computed in main(); we'll use a simple threshold)
    btc_regime = np.full(n, np.nan, dtype=float)
    # premium_zscore and premium_residual are NaN since /futures/data/premiumIndexHistory
    # is rate-banned; LightGBM handles NaN natively
    premium_z = np.full(n, np.nan, dtype=float)
    premium_res = np.full(n, np.nan, dtype=float)

    for i in range(90, n):  # 90-period warmup for the 30d rolling stats
        t = int(ts_f[i])
        e72 = int(np.searchsorted(ts_f, t + H72_MS, side="right")) - 1
        e7d = int(np.searchsorted(ts_f, t + H7D_MS, side="right")) - 1
        if e7d <= i:            # not enough forward history for a full label
            continue
        j = np.searchsorted(ts_p, t, side="right") - 1  # last candle open <= t
        if j < 72:
            continue
        fwd72 = rate[i + 1: e72 + 1]
        fwd7d = rate[i + 1: e7d + 1]
        y72 = float(fwd72.sum()) * 1e4
        y7d = float(fwd7d.sum()) * 1e4
        rows.append([
            sym, t,
            rate[i], rate[i - 1], rate[i - 2], rate[i] - rate[i - 1],
            m3[i], s3[i], m7[i], s7[i], m30[i], z30[i], hot7[i],
            basis[j], bmean[j], bstd[j],
            vol24[j], vol72[j], ret24[j], ret72[j],
            xmean_by_ts.get(t, np.nan), btc_z_by_ts.get(t, np.nan),
            spell[i], pin[i], rate_rank[i], btc_regime[i],
            premium_z[i], premium_res[i],
            y72, y7d,
            int(y7d > EV_MARGIN * COST_BPS),
            int(fwd7d.min() < 0),
        ])
    return rows


def main() -> int:
    syms = sorted({p.name.split("_")[0] for p in CARRY.glob("*_funding.csv")})
    print(f"symbols found: {len(syms)}: {', '.join(syms)}")

    # cross-asset features on the shared settlement instants (rates already
    # normalized per-symbol, so the mean is cadence-comparable)
    # Skip symbols with empty funding files. A 0-row CSV makes load_csv return a
    # 1-D array (np.array([])); indexing f[:, 0] on it crashes. A symbol that
    # fetched nothing (e.g. a 1000X-scaled perp under a plain name) must not
    # poison a pooled build.
    rates: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for s in syms:
        ts, f = load_csv(CARRY / f"{s}_funding.csv", 1)
        if len(ts) == 0:
            print(f"  {s}: SKIPPED (empty funding csv — no realizations)")
            continue
        rates[s] = (ts, f)
    syms = sorted(rates)   # only symbols with usable funding participate hereafter
    norm = {s: (ts, normalize_to_8h(ts, f[:, 0])) for s, (ts, f) in rates.items()}
    btc_ts, btc_rate = norm.get("BTC", (np.array([], dtype=np.int64),
                                        np.array([])))
    btc_m30, btc_s30 = trailing_stats(btc_rate, 90) if len(btc_ts) else (None, None)
    btc_idx = {int(t): k for k, t in enumerate(btc_ts)}

    per_ts_vals: dict[int, list[float]] = {}
    for s, (ts, r) in norm.items():
        for t, v in zip(ts, r):
            per_ts_vals.setdefault(int(t), []).append(v)
    xmean_by_ts = {t: float(np.mean(v)) for t, v in per_ts_vals.items()}
    btc_z_by_ts = {}
    for t, k in btc_idx.items():
        if btc_m30 is not None and not np.isnan(btc_m30[k]) and btc_s30[k] > 1e-12:
            btc_z_by_ts[t] = (btc_rate[k] - btc_m30[k]) / btc_s30[k]

    all_rows = []
    for s in syms:
        if not all((CARRY / f"{s}_{k}.csv").exists()
                   for k in ("funding", "perp_1h", "spot_1h")):
            print(f"  {s}: SKIPPED (incomplete triplet — fetch still running?)")
            continue
        rows = build_symbol(s, btc_z_by_ts, xmean_by_ts)
        print(f"  {s}: {len(rows)} decision rows")
        all_rows.extend(rows)
    all_rows.sort(key=lambda r: (r[1], r[0]))

    # purged walk-forward folds over the sorted ACTUAL row timestamps — the
    # raw funding grid spans 4y but candles (hence rows) span 2y, and every
    # fold must contain rows on both sides of its split
    row_ts = sorted({r[1] for r in all_rows})
    fold_bounds = []
    for tr, te in walk_forward_windows(len(row_ts), N_FOLDS, TRAIN_FRAC):
        fold_bounds.append((row_ts[tr.start], row_ts[tr.stop - 1],
                            row_ts[te.start], row_ts[te.stop - 1]))

    out_rows = []
    for sym, t, *feats in all_rows:
        fold = split = None
        for k, (tr_s, tr_e, te_s, te_e) in enumerate(fold_bounds):
            if tr_s <= t <= te_e:
                fold = k
                split = "train" if t <= tr_e else "test"
                break
        if fold is None:
            continue
        usable = True
        if split == "train":
            # EMBARGO: the 7d forward label must close before the test
            # window opens; drop train rows whose label could straddle it
            if t > tr_e - EMBARGO_MS:
                usable = False
        out_rows.append([sym, t, fold, split, usable] + feats)

    header = ["sym", "ts", "fold", "split", "usable"] + FEATURES + \
             ["y_sum_72h_bps", "y_sum_7d_bps", "y_win", "y_flip"]
    out = CARRY / "dataset.csv"
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(out_rows)
    print(f"\nwrote {out} ({len(out_rows)} rows, {len(header)} cols)")

    # ---- QA ----
    arr = {h: np.array([r[i] for r in out_rows]) for i, h in enumerate(header)}
    test = arr["split"] == "test"
    train_u = (arr["split"] == "train") & arr["usable"]
    print(f"train rows (post-embargo): {int(train_u.sum())} | "
          f"test rows: {int(test.sum())} | "
          f"embargoed train rows dropped: "
          f"{int(((arr['split'] == 'train') & ~arr['usable']).sum())}")
    print(f"y_win rate (all): {arr['y_win'].mean():.3%} | "
          f"y_flip rate (all): {arr['y_flip'].mean():.3%}")

    for k in range(N_FOLDS):
        tr_max = arr["ts"][(arr["fold"] == k) & train_u].max()
        te_min = arr["ts"][(arr["fold"] == k) & test].min()
        gap_h = (te_min - tr_max) / 3.6e6
        assert gap_h > 7 * 24, f"fold {k}: embargo violated ({gap_h:.0f}h)"
    print("embargo check: PASS (every fold's last usable train label closes "
          "before its test window opens by > 7 days)")

    # the incumbent baseline the model must beat: persistence
    # (per-8h-equivalent current rate x 21 periods = 7d forward funding)
    y = arr["y_sum_7d_bps"]
    pers = arr["f0"] * 21 * 1e4
    rmse = lambda a, b: float(np.sqrt(np.nanmean((a - b) ** 2)))  # noqa: E731
    print(f"\nbaseline RMSE on y_sum_7d_bps (test rows):")
    print(f"  predict-zero        : {rmse(y[test], np.zeros_like(y)[test]):8.3f} bps")
    print(f"  naive persistence   : {rmse(y[test], pers[test]):8.3f} bps")
    print("  (any model must beat persistence to justify existing)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
