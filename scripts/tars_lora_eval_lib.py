#!/usr/bin/env python3
"""Shared pure logic for the tars-lora out-of-sample evaluation.

Single source of truth for the Kaggle eval kernel (`notebooks/tars-lora-eval.ipynb`)
and the local pipeline dry-run (`scripts/tars_lora_eval_dryrun.py`). The kernel
VENDORS a copy of this file into the `tars-eval` Kaggle Dataset as `eval_lib.py`,
so what is tested locally is byte-identical to what runs on Kaggle.

Only numpy/pandas — no scipy, no torch. Every quantity here is derived from the
real `data/carry/dataset.csv` (walk-forward, embargoed) and the training-text
format verbatim from `scripts/build_lora_dataset.py`.

Honesty contract (ML_ROADMAP_REVISED.md / src/validation.py):
  * OOS eval set = `split=="test"` rows with the 6 training features present.
  * The 2026 test window is a dead-funding regime: y_win (7d carry > 70 bps)
    fires on ZERO test rows, by construction. The eval therefore reports BOTH
    the calibration/metrics the live gate weighs (Calmar >= 1.0 AND
    has_oos_evidence, via the vendored src/validation.py) AND the signal
    diagnostics (do yes/no scores rank realized carry at all). A policy that
    never trades has no OOS evidence and must NOT clear the gate — that is the
    correct, evidence-based FAIL, not a bug.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# --- Training-format contract (verbatim from scripts/build_lora_dataset.py) ---
FEATURES = ["f0", "basis_bps", "vol_24h", "ret_24h", "f_mean7d", "f_z30d"]
TEMPLATE = (
    "Funding {f0} basis {basis_bps} vol {vol_24h} "
    "ret {ret_24h} 7d_mean {f_mean7d} z {f_z30d} "
    "-> will 7d carry clear costs?"
)

# --- Cost / label contract (verbatim from scripts/build_carry_dataset.py) ----
COST_BPS = 35.0            # round-trip both legs, taker+slippage
EV_MARGIN = 2.0            # y_win requires 7d carry to clear 2x costs
Y_WIN_BAR_BPS = EV_MARGIN * COST_BPS  # 70 bps
INCUMBENT_THRESHOLD = 0.001  # live constant gate (src/agent.py funding_arb_min_rate)


def fmt(x: float) -> str:
    """Identical to scripts/build_lora_dataset.py::_fmt."""
    if isinstance(x, (int, float)) and np.isfinite(float(x)):
        return f"{x:.4f}".rstrip("0").rstrip(".")
    return "0"


def select_eval_rows(df: pd.DataFrame) -> pd.DataFrame:
    """OOS rows the model is allowed to answer: test, usable, 6 features present."""
    mask = (df["split"] == "test") & df["usable"] & df[FEATURES].notna().all(axis=1)
    return df.loc[mask].reset_index(drop=True)


def build_prompts(rows: pd.DataFrame) -> list[str]:
    """Render the exact training-text format (without the answer) for every row."""
    return [
        TEMPLATE.format(f0=fmt(r.f0), basis_bps=fmt(r.basis_bps),
                        vol_24h=fmt(r.vol_24h), ret_24h=fmt(r.ret_24h),
                        f_mean7d=fmt(r.f_mean7d), f_z30d=fmt(r.f_z30d))
        for _, r in rows.iterrows()
    ]


def parse_generated(text: str) -> str:
    """Classify a generated completion as yes/no/other (fail-closed = 'no' upstream)."""
    t = text.strip().lower()
    if t.startswith("yes"):
        return "yes"
    if t.startswith("no"):
        return "no"
    return "other"


def binary_from_score(score: np.ndarray) -> np.ndarray:
    """Decision rule: score is P(yes); >=0.5 trades, <0.5 sits out."""
    return score >= 0.5


def incumbent_enter(rows: pd.DataFrame, threshold: float = INCUMBENT_THRESHOLD) -> np.ndarray:
    """The signal the live agent uses today: f0 <= -threshold (src/agent.py:442)."""
    return (rows["f0"].to_numpy() <= -threshold).astype(bool)


def policy_net_bps(rows: pd.DataFrame, enter: np.ndarray, cost: float = COST_BPS) -> np.ndarray:
    """Per-row net PnL in bps: realized 7d carry minus one round-trip, 0 when flat."""
    enter = np.asarray(enter, dtype=bool)
    y = rows["y_sum_7d_bps"].to_numpy(dtype=float)
    out = np.zeros(len(rows), dtype=float)
    out[enter] = y[enter] - cost
    return out


def portfolio_series(rows: pd.DataFrame, net_bps: np.ndarray):
    """Aggregate per-symbol net to a per-8h portfolio PnL series (sum across symbols).

    Returns (ts, pnl) sorted ascending by ts, one value per 8h period.
    """
    frame = pd.DataFrame({"ts": rows["ts"].to_numpy(), "r": net_bps})
    g = frame.groupby("ts")["r"].sum().sort_index()
    return g.index.to_numpy(), g.values.astype(float)


def trades_summary(rows: pd.DataFrame, enter: np.ndarray, cost: float = COST_BPS) -> dict:
    net = policy_net_bps(rows, enter, cost)
    n = int(np.count_nonzero(enter))
    return {
        "trades": n,
        "trade_rate_pct": round(100.0 * n / len(rows), 4) if len(rows) else 0.0,
        "total_net_bps": round(float(net.sum()), 2),
        "mean_net_bps_when_traded": round(float(net[np.nonzero(net)].mean()), 2) if n else None,
        "mean_y_sum_7d_when_traded": round(
            float(rows["y_sum_7d_bps"].to_numpy()[enter].mean()), 2) if n else None,
    }


def gate_report(ts_pnl, ts_pnl_prev_steps=None):
    """Run the live validation gate on the portfolio series.

    Uses the vendored src/validation.py (single source of truth with the repo).
    `ts_pnl` is the OOS portfolio PnL per 8h period in bps. validation_report
    builds a MULTIPLICATIVE equity curve (cumprod(1+returns)), so bps-scale
    values (which exceed 100%) must be converted to fractional per-period
    returns on notional: /10000. The gate code itself is left untouched.
    has_oos_evidence guards the vacuous-PASS bug (validation.py:154).
    """
    import validation as V  # vendored from src/validation.py into the eval dataset

    rets = np.asarray(ts_pnl, dtype=float) / 10000.0  # bps -> fractional return
    report = V.validation_report(rets, rets, calmar_bar=1.0)
    return {
        "oos_trades_nonzero_periods": int(np.count_nonzero(ts_pnl)),
        "has_oos_evidence": bool(report["has_oos_evidence"]),
        "out_of_sample": report["out_of_sample"],
        "passes_calmar_bar": bool(report["passes_calmar_bar"]),
        "cleared_for_paper_trading": bool(report["cleared_for_paper_trading"]),
    }


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Pure-numpy Spearman rank correlation (no scipy dependency)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    rx = rankdata(x)
    ry = rankdata(y)
    n = len(rx)
    if n < 3:
        return 0.0
    d = rx - ry
    denom = n * (n * n - 1)
    return float(1.0 - 6.0 * float(np.sum(d * d)) / denom)


def rankdata(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1)
    return ranks


def decile_table(score: np.ndarray, y: np.ndarray, k: int = 10) -> list[dict]:
    """Mean realized 7d carry by model-score decile (bins on the score axis)."""
    score = np.asarray(score, dtype=float)
    y = np.asarray(y, dtype=float)
    bins = np.quantile(score, np.linspace(0, 1, k + 1))
    idx = np.clip(np.searchsorted(bins, score, side="right") - 1, 0, k - 1)
    out = []
    for d in range(k):
        sel = idx == d
        if not np.any(sel):
            out.append({"decile": d, "n": 0})
            continue
        out.append({
            "decile": d,
            "n": int(np.count_nonzero(sel)),
            "score_min": round(float(score[sel].min()), 4),
            "score_max": round(float(score[sel].max()), 4),
            "y_mean_bps": round(float(y[sel].mean()), 2),
            "y_mean_net_bps": round(float(y[sel].mean() - COST_BPS), 2),
        })
    return out


def diagnostics(score: np.ndarray, y: np.ndarray) -> dict:
    """Signal diagnostics for a regime where the binary label is (nearly) degenerate."""
    score = np.asarray(score, dtype=float)
    y = np.asarray(y, dtype=float)
    enter = binary_from_score(score)
    n = len(score)
    top5 = score >= np.quantile(score, 0.95)
    bot5 = score <= np.quantile(score, 0.05)
    return {
        "n_rows": n,
        "score_p10_p50_p90": [
            round(float(q), 4) for q in np.quantile(score, [0.1, 0.5, 0.9])
        ],
        "enter_rate_pct": round(100.0 * float(np.mean(enter)), 4),
        "spearman_score_vs_y_sum_7d": round(spearman(score, y), 4),
        "mean_y_entered_bps": round(float(y[enter].mean()), 2) if np.any(enter) else None,
        "mean_y_not_entered_bps": round(float(y[~enter].mean()), 2) if np.any(~enter) else None,
        "delta_bps_entered_minus_not": round(
            float(y[enter].mean() - y[~enter].mean()), 2) if np.any(enter) and np.any(~enter) else None,
        "top5pct_mean_y_bps": round(float(y[top5].mean()), 2),
        "bot5pct_mean_y_bps": round(float(y[bot5].mean()), 2),
        "top5_minus_bot5_bps": round(float(y[top5].mean() - y[bot5].mean()), 2),
        "deciles": decile_table(score, y),
    }


def cost_sensitivity(rows: pd.DataFrame, enter: np.ndarray,
                     costs=(16.0, 35.0, 50.0)) -> dict:
    """Net bps / trade count at cheap, canonical, and stress round-trip costs."""
    out = {}
    for c in costs:
        net = policy_net_bps(rows, enter, cost=c)
        out[str(int(c))] = {
            "trades": int(np.count_nonzero(enter)),
            "total_net_bps": round(float(net.sum()), 2),
        }
    return out