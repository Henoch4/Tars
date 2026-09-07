#!/usr/bin/env python3
"""
ML training for carry model — Quantile (τ=0.2, 0.5) + Discrete Hazard.
Train on data/carry/dataset.csv, evaluate via src/validation.py gates.
Priority 1 → 2 → 3 per research plan.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.validation import (
    calmar_ratio,
    max_drawdown,
    sharpe_ratio,
    validation_report,
    walk_forward_windows,
)

REPO_ROOT = REPO
DATA_DIR = REPO_ROOT / "data"
CARRY_CSV = DATA_DIR / "carry" / "dataset.csv"
MODELS_DIR = REPO_ROOT / "models"
REPORTS_DIR = REPO_ROOT / "reports"

MODELS_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

# ============================================================
# Config
# ============================================================
HORIZON_DAYS = 7
FEE_BPS = 5.0
SLIP_BPS = 3.0
SIDE_COST = (FEE_BPS + SLIP_BPS) / 10000.0
BARS_PER_8H = 8
N_FOLDS = 6
TRAIN_FRAC = 0.7
CALMAR_BAR = 1.0
PINBALL_TAU = [0.2, 0.5]

# Quantile gate
Q20_THRESHOLD_BPS = 16.0

# LightGBM params
QUANTILE_PARAMS = dict(
    objective="quantile",
    alpha=0.2,  # will override per-tau
    n_estimators=500,
    learning_rate=0.03,
    num_leaves=31,
    max_depth=5,
    min_child_samples=50,
    feature_fraction=0.8,
    bagging_fraction=0.8,
    bagging_freq=1,
    verbose=-1,
    force_row_wise=True,
)

HAZARD_PARAMS = dict(
    objective="binary",
    n_estimators=300,
    learning_rate=0.05,
    num_leaves=15,
    max_depth=3,
    min_child_samples=50,
    feature_fraction=0.8,
    bagging_fraction=0.8,
    bagging_freq=1,
    verbose=-1,
    force_row_wise=True,
)

# ============================================================
# Data loading
# ============================================================
def load_carry_dataset():
    df = pd.read_csv(CARRY_CSV)
    # Only usable rows
    df = df[df["usable"] == True].copy()
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    return df


# ============================================================
# Feature engineering
# ============================================================
FEATURE_COLS = [
    "f0", "f1", "f2", "f_del", "f_mean3d", "f_std3d",
    "f_mean7d", "f_std7d", "f_mean30d", "f_z30d", "f_hot7d",
    "basis_bps", "basis_mean24h", "basis_std24h",
    "vol_24h", "vol_72h", "ret_24h", "ret_72h",
    "x_mean_f", "x_btc_z", "spell_age",
    "pin_state", "funding_rank_cross", "btc_funding_regime",
    "premium_zscore", "premium_residual",
]

def prepare_features(df: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    # Fill missing
    X = df[FEATURE_COLS].copy()
    for col in X.columns:
        if X[col].dtype in ("float64", "float32", "int64", "int32"):
            X[col] = X[col].fillna(X[col].median())
    return X.to_numpy(), FEATURE_COLS


def build_labels(df: pd.DataFrame):
    y = df["y_sum_7d_bps"].to_numpy(dtype=np.float32)
    y_win = df["y_win"].to_numpy(dtype=np.int32)  # 0/1
    y_flip = df["y_flip"].to_numpy(dtype=np.int32)
    return y, y_win, y_flip


# ============================================================
# Quantile regression (Pinball loss)
# ============================================================
def train_quantile(Xtr, ytr, tau, **kw):
    params = {**QUANTILE_PARAMS, "alpha": tau}
    params.update(kw)
    m = lgb.LGBMRegressor(**params)
    m.fit(Xtr, ytr)
    return m


def fit_quantile_models(Xtr, ytr):
    m20 = lgb.LGBMRegressor(**{**QUANTILE_PARAMS, "alpha": 0.2})
    m50 = lgb.LGBMRegressor(**{**QUANTILE_PARAMS, "alpha": 0.5})
    m20.fit(Xtr, ytr)
    m50.fit(Xtr, ytr)
    return m20, m50


def quantile_coverage(y, q, tau):
    if len(y) != len(q):
        return float("nan")
    return np.mean(y < q)


# ============================================================
# Discrete Hazard / Survival
# ============================================================
def build_hazard_dataset(df: pd.DataFrame, horizon: int = 7):
    """
    Build person-period dataset for discrete hazard.
    Each row = (spell_id, spell_age, flipped, features...).
    Spell = consecutive bars where y_sum_7d_bps > threshold (or funding edge).
    """
    # Define spell: funding edge active (y_win == 1)
    df = df.copy()
    df["spell_id"] = (df["y_win"] != df["y_win"].shift()).cumsum()
    df["spell_age"] = df.groupby("spell_id").cumcount()

    # Flip = first bar where y_win == 0 after a 1-run
    df["flipped"] = 0
    # Mark end of each spell (last bar before y_win goes 0)
    spell_ends = df[df["y_win"].shift(-1) == 0]
    df.loc[spell_ends.index, "flipped"] = 1

    # Only keep rows where spell is alive (y_win == 1) and not the flipped bar
    alive = df[df["y_win"] == 1].copy()
    # Censored: last bar of each spell that doesn't flip (or end of data)
    return alive


def train_hazard_model(alive_df: pd.DataFrame, feature_cols):
    # spell_age as categorical or spline
    X = pd.get_dummies(alive_df[["spell_age"] + feature_cols], columns=["spell_age"], prefix="age")
    y = alive_df["flipped"].astype(int)
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    model = LogisticRegression(max_iter=1000, C=1.0, penalty="l2")
    model.fit(X_scaled, y)
    return model, scaler


def survival_curve(hazards: np.ndarray) -> np.ndarray:
    return np.cumprod(1 - hazards)


def expected_carry_pnl(funding: np.ndarray, hazards: np.ndarray, cost_per_period: float = 0.0) -> float:
    s_prev = 1.0
    exp = 0.0
    for f, h in zip(funding, hazards):
        exp += (1 - h) * (f - cost_per_period)  # S_{t-1} * (funding - cost)
        # Note: survival S_{t-1} = prod_{j<t}(1-h_j)
        # We approximate: at step t, survival so far is product of (1-h) up to t-1
        # This is an approximation - full implementation needs proper S_{t-1} tracking
    return 0.0  # placeholder for now


# ============================================================
# Validation gates
# ============================================================
def run_walkforward_validation(X, y, n_folds=6, train_frac=0.7):
    from src.validation import validation_report, walk_forward_windows

    n = len(y)
    results = []
    for tr, te in walk_forward_windows(n, n_folds, train_frac):
        Xtr, ytr = X[tr], y[tr]
        Xte, yte = X[te], y[te]

        # Train quantile models
        m20, m50 = fit_quantile_models(Xtr, ytr)

        q20_te = m20.predict(Xte)
        q50_te = m50.predict(Xte)

        # Gate: q20 > 16 bps
        gate_pass = np.mean(m20.predict(Xte) > 16.0) > 0.5  # majority pass

        # Pinball losses
        pinball_20 = np.mean(np.maximum(0.2 * (yte - q20_te), (0.2 - 1) * (yte - q20_te)))
        pinball_50 = np.mean(np.maximum(0.5 * (yte - q50_te), (0.5 - 1) * (yte - q50_te)))

        results.append({
            "pinball_20": float(pinball_20),
            "pinball_50": float(pinball_50),
            "q20_coverage": float(np.mean(y[te] < m20.predict(X[te]))),
            "gate_pass": gate_pass,
        })
    return results


# ============================================================
# Model persistence
# ============================================================
def save_models(models_dict: dict, path: Path):
    """Save LightGBM models to disk."""
    import joblib
    path.mkdir(parents=True, exist_ok=True)
    for name, model in models_dict.items():
        joblib.dump(model, path / f"{name}.pkl")
    print(f"Saved {len(models_dict)} models to {path}")


def load_models(path: Path) -> dict:
    import joblib
    models = {}
    for p in path.glob("*.pkl"):
        name = p.stem
        models[name] = joblib.load(p)
    return models


# ============================================================
# Main
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTC,ETH,SOL,BNB")
    ap.add_argument("--save", action="store_true", help="Save trained models")
    ap.add_argument("--load", action="store_true", help="Load existing models")
    args = ap.parse_args()

    print("Loading carry dataset...")
    df = load_carry_dataset()
    print(f"Loaded {len(df)} rows, {df['sym'].nunique()} symbols")

    X, feat_names = prepare_features(df)
    y_sum, y_win, y_flip = build_labels(df)

    print(f"Features: {len(feat_names)}")

    # Walk-forward validation
    print("\nRunning walk-forward validation...")
    wf_results = run_walkforward_validation(X, y_sum)
    for i, r in enumerate(wf_results):
        print(f"Fold {i}: pinball_20={r['pinball_20']:.4f}, pinball_50={r['pinball_50']:.4f}, "
              f"q20_cov={r['q20_coverage']:.3f}, gate={r['gate_pass']}")

    # Train final models on full dataset
    print("\nTraining final models on full dataset...")
    m20, m50 = fit_quantile_models(X, y_sum)
    
    # Save models
    if args.save:
        models_dir = MODELS_DIR / "carry_quantile"
        save_models({"q20": m20, "q50": m50}, models_dir)
        
        # Also save feature names
        import joblib
        joblib.dump(FEATURE_COLS, MODELS_DIR / "carry_quantile" / "features.pkl")
        print(f"Saved feature list to {MODELS_DIR}/carry_quantile/features.pkl")

    # Quick inference test
    print("\nRunning inference test...")
    q20 = m20.predict(X[:5])
    q50 = m50.predict(X[:5])
    print(f"Sample q20: {q20}")
    print(f"Sample q50: {q50}")
    print(f"q20 > 16: {(q20 > 16).sum()} / 5")

    # Gate check
    q20_all = m20.predict(X)
    gate_pass_rate = (q20_all > 16).mean()
    print(f"Overall q20 > 16 rate: {gate_pass_rate:.3f}")

    return 0


if __name__ == "__main__":
    main()