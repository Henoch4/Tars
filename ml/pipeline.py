#!/usr/bin/env python3
"""ML pipeline v2 -- spike follow-up per ML_ROADMAP docs.

Two-layer design (AFML meta-labeling), evaluated only inside walk-forward
test slices, gated by the repo's own src/validation.py at 5+3 bps/side:

  layer 1 (primary): LightGBM direction model on Alpha158-adapted features,
      trained on triple-barrier outcomes (+/-1), uniqueness-weighted
  layer 2 (meta):    LightGBM "will this trade succeed" filter, trained on
      inner-OOF primary probabilities (no leakage), gating which primary
      signals become trades at all

Trade simulation is event-driven: a taken trade enters at close[t], exits at
the precomputed barrier/vertical exit of its label window -- one round trip
per signal, one open position at a time. This is the mechanism intended to
cut v1's 2,119 position changes by ~10x (reports/ml-spike-2026-08-22.md).

Fixed-by-design hyperparameters (no sweep, PBO discipline); the only grid is
the meta-probability threshold, tracked through evaluate_parameter_grid.

Usage:  python -m ml.pipeline --symbols BTC,ETH,SOL,BNB
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.validation import (  # noqa: E402
    evaluate_parameter_grid,
    sharpe_ratio,
    validation_report,
    walk_forward_windows,
)
from ml.features import build_features, load_symbol  # noqa: E402
from ml.labeling import triple_barrier, uniqueness_weights  # noqa: E402

import lightgbm as lgb  # noqa: E402

HORIZON = 24                 # barrier window (bars) -- 24h swing horizon
PRIMARY_EDGE = 0.05          # primary fires direction when |p - 0.5| > edge
Q_THRESHOLDS = [0.55, 0.60, 0.65]
FEE_BPS, SLIP_BPS = 5.0, 3.0
SIDE_COST = (FEE_BPS + SLIP_BPS) / 10_000.0
N_FOLDS, TRAIN_FRAC = 6, 0.7
BARS_PER_8H = 8
CALMAR_BAR = 1.0

PRIMARY_PARAMS = dict(n_estimators=300, learning_rate=0.05, num_leaves=31,
                      min_child_samples=50, feature_fraction=0.8,
                      bagging_fraction=0.8, bagging_freq=1, verbose=-1)
META_PARAMS = dict(n_estimators=200, learning_rate=0.05, num_leaves=15,
                   min_child_samples=100, feature_fraction=0.8,
                   bagging_fraction=0.8, bagging_freq=1, verbose=-1)


def fit_primary(Xtr, ytr, wtr):
    m = lgb.LGBMClassifier(**PRIMARY_PARAMS)
    m.fit(Xtr, ytr, sample_weight=wtr)
    return m


def inner_oof_probs(Xtr, ytr, wtr, n_splits=3):
    """Out-of-fold primary probabilities on the train set itself, so the meta
    model never trains on the primary's in-sample overfit probabilities."""
    p = np.full(len(ytr), np.nan)
    cuts = np.linspace(0, len(ytr), n_splits + 1, dtype=int)
    for k in range(n_splits):
        lo, hi = cuts[k], cuts[k + 1]
        keep = np.ones(len(ytr), dtype=bool)
        keep[max(0, lo - HORIZON):hi] = False        # purge overlap
        if keep.sum() < 500 or (hi - lo) == 0:
            continue
        mk = fit_primary(Xtr[keep], ytr[keep], wtr[keep])
        p[lo:hi] = mk.predict_proba(Xtr[lo:hi])[:, 1]
    return p


def simulate_trades(signals: pd.DataFrame, pos_out: np.ndarray) -> dict:
    """signals: rows (t, direction, q) sorted by t; take one at a time, fill
    pos_out[bar] with direction while holding. Returns trade stats."""
    trades = []
    busy_until = -1
    for t, d, q in signals.itertuples(index=False):
        if t < busy_until or not np.isfinite(q):
            continue
        xj = int(EXIT_J[t])
        trades.append((t, d, float(RET_LONG[t])))
        pos_out[t:xj] = d
        busy_until = xj
    if not trades:
        return {"trades": 0}
    t_arr = np.array([a for a, _, _ in trades])
    d_arr = np.array([b for _, b, _ in trades])
    r_arr = np.array([q_ for _, _, q_ in trades])
    wins = int(((r_arr * d_arr) > 0).sum())
    return {
        "trades": len(trades),
        "win_rate": round(wins / len(trades), 4),
        "avg_net_ret_per_trade": round(float(np.mean(d_arr * r_arr) - 2 * SIDE_COST), 6),
        "accuracy_vs_outcome": round(float(np.mean(d_arr == OUTCOME[t_arr])), 4),
    }


def net_bar_returns(pos: np.ndarray, close: np.ndarray) -> np.ndarray:
    ret = pos[:-1] * (np.diff(close) / close[:-1])
    ret -= np.abs(np.diff(pos)) * SIDE_COST
    if len(pos) and pos[-1] != 0:
        ret[-1] -= abs(pos[-1]) * SIDE_COST
    return ret


def to_8h(bar_ret: np.ndarray) -> np.ndarray:
    n = (len(bar_ret) // BARS_PER_8H) * BARS_PER_8H
    return np.prod(1 + bar_ret[:n].reshape(-1, BARS_PER_8H), axis=1) - 1


def run_symbol(sym: str) -> dict:
    global EXIT_J, RET_LONG, OUTCOME
    df = load_symbol(REPO, sym)
    X, names = build_features(df)
    ev = triple_barrier(df, horizon=HORIZON, m=1.0)
    OUTCOME = ev["outcome"].to_numpy()
    EXIT_J = ev["exit_j"].to_numpy()
    RET_LONG = ev["ret_long"].to_numpy()

    valid = np.isfinite(X.to_numpy()).all(axis=1) & (OUTCOME != 0) & (EXIT_J > 0)
    rows = np.where(valid)[0]
    Xv = X.to_numpy()[rows]
    yv = ((OUTCOME[rows] + 1) / 2).astype(int)        # {0,1} for lgbm
    wv = uniqueness_weights(rows, HORIZON)

    n_obs = len(rows)
    sig_rows = []                                     # (row_idx, direction, q)
    sig_is_rows = []                                  # IS twin, meta-gated
    for tr, te in walk_forward_windows(n_obs, N_FOLDS, TRAIN_FRAC):
        te_rows = rows[te]
        keep = rows[tr] < te_rows[0] - HORIZON          # purge label overlap
        tr_pos = np.arange(tr.start, tr.stop)[keep]
        if len(tr_pos) < 1000 or len(te_rows) == 0:
            continue
        te_pos = np.arange(te.start, te.stop)
        Xtr, ytr, wtr_p = Xv[tr_pos], yv[tr_pos], wv[tr_pos]
        # ---- layer 1: primary ----
        pm = fit_primary(Xtr, ytr, wtr_p)
        p_is = pm.predict_proba(Xtr)[:, 1]
        p_te = pm.predict_proba(Xv[te_pos])[:, 1]
        # ---- layer 2: meta on inner-OOF primary probs ----
        p_oof = inner_oof_probs(Xtr, ytr, wtr_p)
        fire = np.abs(p_oof - 0.5) > PRIMARY_EDGE
        q_te = np.full(len(te_rows), np.nan)
        meta = None
        if fire.sum() >= 500:
            dir_tr = np.where(p_oof[fire] > 0.5, 1, -1)
            y_meta = (dir_tr == np.where(ytr[fire] == 1, 1, -1)).astype(int)
            X_meta = np.column_stack([Xtr[fire], p_oof[fire]])
            meta = lgb.LGBMClassifier(**META_PARAMS)
            meta.fit(X_meta, y_meta)
            fire_te = np.abs(p_te - 0.5) > PRIMARY_EDGE
            if fire_te.any():
                q_te[fire_te] = meta.predict_proba(
                    np.column_stack([Xv[te_pos][fire_te], p_te[fire_te]]))[:, 1]
        # ---- record ----
        d_te = np.where(p_te > 0.5, 1, -1)
        for k in np.where(np.abs(p_te - 0.5) > PRIMARY_EDGE)[0]:
            sig_rows.append((te_rows[k], d_te[k], q_te[k]))
        d_is = np.where(p_is > 0.5, 1, -1)
        fire_is = np.abs(p_is - 0.5) > PRIMARY_EDGE
        # IS meta-gate: predict q on the fired train rows with the SAME fitted
        # meta model (features use the primary's in-sample probs, which are
        # overconfident by construction -- that optimism is the point: PBO
        # needs an IS view that can differ from OOS. The previous version
        # hardcoded q=0.99, making every threshold's IS series identical and
        # the PBO grid a tie-breaking artifact.)
        q_is = np.full(int(fire_is.sum()), np.nan)
        if meta is not None and fire_is.any():
            q_is = meta.predict_proba(
                np.column_stack([Xtr[fire_is], p_is[fire_is]]))[:, 1]
        sig_is_rows.extend(zip(rows[tr_pos][fire_is], d_is[fire_is], q_is))

    sig = pd.DataFrame(sig_rows, columns=["t", "direction", "q"]).sort_values("t")
    sig_is = pd.DataFrame(sig_is_rows, columns=["t", "direction", "q"]).sort_values("t")
    close = df["close"].to_numpy()
    results = {"symbol": sym, "bars": int(len(df)), "features": len(names),
               "oos_signals": int(len(sig))}
    grid_is, grid_oos = {}, {}
    for thr in Q_THRESHOLDS:
        pos = np.zeros(len(df))
        stats = simulate_trades(sig[sig["q"] >= thr].reset_index(drop=True), pos)
        pos_is_thr = np.zeros(len(df))
        simulate_trades(sig_is[sig_is["q"] >= thr].reset_index(drop=True), pos_is_thr)
        is8 = to_8h(net_bar_returns(pos_is_thr, close))
        ret8 = to_8h(net_bar_returns(pos, close))
        rep = validation_report(is8, ret8, calmar_bar=CALMAR_BAR)
        results[f"thr_{thr}"] = {"trade_stats": stats, "validation": rep,
                                 "net_full_reinvest": round(float(
                                     np.prod(1 + net_bar_returns(pos, close)) - 1), 4)}
        grid_is[f"q>={thr}"] = is8
        grid_oos[f"q>={thr}"] = ret8
    pbo = evaluate_parameter_grid(grid_is, grid_oos)
    results["pbo_grid"] = {"n_combinations_tested": pbo["n_combinations_tested"],
                           "pbo": pbo["pbo"], "pbo_pass": pbo["pbo_pass"]}
    # honest selection: best IS Sharpe -> report its OOS verdict in headline
    best = max(Q_THRESHOLDS, key=lambda thr: sharpe_ratio(grid_is[f"q>={thr}"]))
    results["selected_threshold_is"] = best
    results["selected_oos"] = results[f"thr_{best}"]
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTC,ETH,SOL,BNB")
    args = ap.parse_args()
    all_results = {}
    portfolio: dict[str, np.ndarray] = {}
    for sym in args.symbols.split(","):
        r = run_symbol(sym.strip())
        all_results[sym.strip()] = r
        print(f"[{sym}] trades@sel={r['selected_oos']['trade_stats'].get('trades', 0)} "
              f"calmar={r['selected_oos']['validation']['out_of_sample']['calmar']:.2f} "
              f"pbo={r['pbo_grid']['pbo']:.2f}", flush=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = REPO / "reports" / f"ml-v2-{date}.md"
    slim = {k: {kk: vv for kk, vv in v.items() if kk != "selected_oos"}
            for k, v in all_results.items()}
    out.write_text(
        f"# ML pipeline v2 (meta-labeling) — {date}\n\n"
        f"```json\n{json.dumps(slim, indent=2, default=str)}\n```\n\n"
        f"Selected (best IS Sharpe) OOS results per symbol:\n\n"
        + "\n".join(
            f"- **{k}** thr={v['selected_threshold_is']}: "
            f"{json.dumps(v['selected_oos']['trade_stats'])} "
            f"OOS calmar={v['selected_oos']['validation']['out_of_sample']['calmar']:.2f}"
            for k, v in all_results.items()) + "\n"
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
