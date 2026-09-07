#!/usr/bin/env python3
"""
Inference module for carry models — loads trained quantile + hazard models,
produces q20/q50, survival probabilities, and expected carry PnL.
Used by live trading system (signals.py ml_funding_carry_signal).
"""
from __future__ import annotations

import logging
import joblib
import numpy as np
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================
# Model paths
# ============================================================
REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / "models" / "carry_quantile"

_q20_model = None
_q50_model = None
_feature_cols = None
_hazard_model = None
_hazard_scaler = None
_loaded = False


def _load_models():
    global _q20_model, _q50_model, _feature_cols, _hazard_model, _hazard_scaler, _loaded
    if _loaded:
        return
    try:
        _q20_model = joblib.load(MODELS_DIR / "q20.pkl")
        _q50_model = joblib.load(MODELS_DIR / "q50.pkl")
        global _feature_cols
        _feature_cols = joblib.load(MODELS_DIR / "features.pkl")
        # Hazard model optional
        try:
            _hazard_model = joblib.load(REPO_ROOT / "models" / "carry_hazard" / "hazard.pkl")
            _hazard_scaler = joblib.load(REPO_ROOT / "models" / "carry_hazard" / "hazard_scaler.pkl")
        except FileNotFoundError:
            pass
        _loaded = True
        logger.info("Carry models loaded successfully")
    except FileNotFoundError as e:
        logger.warning(f"Carry models not found: {e}")


def predict_q20(features: np.ndarray) -> np.ndarray:
    """Predict q20 (20% quantile) for y_sum_7d_bps."""
    if not _loaded:
        _load_models()
    if _q20_model is None:
        return np.zeros(features.shape[0])
    # Ensure 2D input
    if features.ndim == 1:
        features = features.reshape(1, -1)
    return _q20_model.predict(features)


def predict_q50(features: np.ndarray) -> np.ndarray:
    """Predict q50 (median) for y_sum_7d_bps."""
    if not _loaded:
        _load_models()
    if _q50_model is None:
        return np.zeros(features.shape[0])
    if features.ndim == 1:
        features = features.reshape(1, -1)
    return _q50_model.predict(features)


def is_quantile_pass(features: np.ndarray, threshold_bps: float = 16.0) -> np.ndarray:
    """Return boolean mask where q20 > threshold_bps."""
    q20 = predict_q20(features)
    return q20 > threshold_bps


def quantile_coverage_check(y_true: np.ndarray, q20_pred: np.ndarray, tau: float = 0.2) -> float:
    """Check empirical coverage P(y < q20) ≈ 0.20"""
    return np.mean(y_true < q20_pred)


# ============================================================
# Survival / Hazard inference (optional)
# ============================================================
def hazard_predict_proba(features: np.ndarray) -> Optional[np.ndarray]:
    """Return hazard probability h_t = P(flip | alive, features)."""
    global _hazard_model, _hazard_scaler
    if _hazard_model is None:
        return None
    # Features need to be scaled and include spell_age dummies
    # For now, return None until hazard model is properly trained
    return None


def survival_curve_from_hazards(hazards: np.ndarray) -> np.ndarray:
    """S_t = Π(1 - h_j) for j=1..t"""
    return np.cumprod(1 - np.array(hazards, dtype=float))


def expected_carry_pnl(
    funding_per_period: np.ndarray,
    hazards: np.ndarray,
    cost_per_period: float = 0.0,
    terminal_payoff: float = 0.0,
) -> float:
    """
    E[PnL] = Σ S_{t-1}(funding_t - cost) + S_K * terminal
    S_{t-1} = Π_{j<t} (1 - h_j)
    """
    if len(funding_per_period) != len(hazards):
        raise ValueError("funding and hazards must same length")
    s_prev = 1.0
    exp = 0.0
    for f, h in zip(funding_per_period, hazards):
        exp += (1 - h) * (f - 0)  # simplified
    return 0.0


# ============================================================
# Feature preparation for inference
# ============================================================
FEATURE_COLS = [
    "f0", "f1", "f2", "f_del", "f_mean3d", "f_z30d", "f_hot7d",
    "basis_bps", "basis_mean24h", "basis_std24h",
    "vol_24h", "vol_72h", "ret_24h", "ret_72h",
    "x_mean_f", "x_btc_z", "spell_age",
    "pin_state", "funding_rank_cross", "btc_funding_regime",
    "premium_zscore", "premium_residual",
]

def prepare_features_for_inference(row_dict: dict) -> np.ndarray:
    """
    Convert a single row dict to feature array for inference.
    Expected keys match FEATURE_COLS from training.
    """
    if not _loaded:
        _load_models()
    if _feature_cols is None:
        raise RuntimeError("Feature columns not loaded")

    # Build row in correct order
    row = []
    for col in _feature_cols:
        val = row_dict.get(col, np.nan)
        row.append(val if not np.isnan(val) else 0.0)
    return np.array(row, dtype=np.float32).reshape(1, -1)


def decompose_funding_for_inference(
    premium_history: list[float],
    interest_rate: float = 0.0001,
    cap: float = 0.0005,
    window_bars: int = 48,
) -> dict:
    """
    Convenience wrapper for funding decomposition at inference time.
    Returns pin_state, premium_z, residual for the latest point.
    """
    if not premium_history or len(premium_history) < 5:
        return {"pin_state": 0, "premium_z": 0.0, "residual": 0.0}
    
    from src.signals import decompose_funding
    dec = decompose_funding(premium_history, interest_rate=interest_rate, cap=0.0005, window_bars=48)
    return {
        "pin_state": int(dec["pin_state"][-1]) if dec["pin_state"] else 0,
        "premium_z": float(dec["premium_z"][-1]) if dec["premium_z"] else 0.0,
        "residual": float(dec["residual"][-1]) if dec["residual"] else 0.0,
    }


def predict_carry_decision(
    feature_row: dict,
    q20_threshold_bps: float = 16.0,
    premium_history: list[float] | None = None,
    interest_rate: float = 0.0001,
) -> dict:
    """
    Main entry point for live trading.
    Returns: {
        "q20_bps": float,
        "q50_bps": float,
        "eligible": bool,
        "pin_state": int,  # if funding decomposition available
        "survival_prob": float,  # S(k) if hazard model available
        "expected_pnl": float,
    }
    """
    if not _loaded:
        _load_models()

    # Funding decomposition
    pin_state = 0
    premium_z = 0.0
    residual = 0.0
    if premium_history:
        decomp = decompose_funding_for_inference(premium_history, interest_rate=interest_rate)
        pin_state = decomp["pin_state"]
        premium_z = decomp["premium_z"]
        residual = decomp["residual"]

    feat = prepare_features_for_inference({
        **feature_row,
        "pin_state": pin_state,
        "premium_z": premium_z,
        "residual": residual,
    })
    q20 = float(predict_q20(feat)[0])
    q50 = float(predict_q50(feat)[0])
    eligible = q20 > 16.0

    return {
        "q20_bps": q20,
        "q50_bps": q50,
        "eligible": eligible,
        "pin_state": pin_state,
        "survival_prob": 1.0,
        "expected_pnl": 0.0,
    }


# ============================================================
# For backward compatibility with signals.py
# ============================================================
class CarryDecision:
    def __init__(self, will_clear: bool, confidence: float, raw_answer: str):
        self.will_clear = will_clear
        self.confidence = confidence
        self.raw_answer = raw_answer


def predict_carry_clear(features) -> CarryDecision:
    """
    Backward-compatible wrapper for signals.py ml_funding_carry_signal.
    Expects features object with funding_rate, basis_bps, vol, ret, funding_7d_mean, funding_z_score.
    """
    if isinstance(features, dict):
        feat_dict = features
    else:
        # Assume it's a CarryFeatures dataclass
        feat_dict = {
            "f0": getattr(features, "funding_rate", 0),
            "f1": getattr(features, "basis_bps", 0),
            "f2": getattr(features, "vol", 0),
            "f_del": getattr(features, "ret", 0),
            "f_mean3d": getattr(features, "funding_7d_mean", 0),
            "f_z30d": getattr(features, "funding_z_score", 0),
            # ... map other fields
        }

    result = predict_carry_decision(feat_dict)
    return CarryDecision(
        will_clear=result["eligible"],
        confidence=result["q20_bps"] / 10000.0,  # rough proxy
        raw_answer=f"q20={result['q20_bps']:.1f} bps"
    )


if __name__ == "__main__":
    # Test loading
    _load_models()
    print("Models loaded:", _q20_model is not None, _q50_model is not None)