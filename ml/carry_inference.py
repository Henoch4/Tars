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
_model_meta: dict = {}
_hazard_model = None
_hazard_scaler = None
_loaded = False

# Default retraining window (ml/RETRAINING_POLICY.md: weekly retrain,
# monthly gate): artifacts older than this are expired for entry, not
# merely stale. Overridable per-call; the training timestamp comes from
# the meta.json sidecar, never file mtime.
MODEL_MAX_AGE_S = 30 * 86400


class FeatureDriftError(RuntimeError):
    """Feature schema drift: the persisted model schema and the inference
    input disagree (missing columns, wrong width, corrupt artifact, absent
    artifacts). Raised — never defaulted — so a stale model cannot silently
    produce garbage. A RuntimeError subclass so existing fail-closed callers
    (`except Exception` -> typed NEUTRAL degradation) catch it as
    `ml_unavailable:FeatureDriftError`."""


def validate_feature_schema(cols) -> list:
    """Pure check: a persisted feature schema must be a non-empty list of
    non-empty unique strings. Returns the list; raises FeatureDriftError on
    any drift/corruption (bool counts as wrong-type: True-as-1 must not
    become a column name)."""
    if isinstance(cols, bool) or not isinstance(cols, list) or not cols:
        raise FeatureDriftError(
            f"feature schema must be a non-empty list, got {type(cols).__name__}"
        )
    for c in cols:
        if isinstance(c, bool) or not isinstance(c, str) or not c:
            raise FeatureDriftError(
                f"feature schema holds invalid column {c!r} "
                f"(type {type(c).__name__})"
            )
    if len(set(cols)) != len(cols):
        dupes = sorted({c for c in cols if cols.count(c) > 1})
        raise FeatureDriftError(f"feature schema has duplicate columns {dupes}")
    return cols


def _load_models():
    global _q20_model, _q50_model, _feature_cols, _model_meta, _hazard_model, _hazard_scaler, _loaded
    if _loaded:
        return
    try:
        _q20_model = joblib.load(MODELS_DIR / "q20.pkl")
        _q50_model = joblib.load(MODELS_DIR / "q50.pkl")
        _feature_cols = validate_feature_schema(joblib.load(MODELS_DIR / "features.pkl"))
        _model_meta = _load_model_meta()
        # Train/artifact loop closure (4.8): the sidecar's schema must agree
        # with the artifact schema — a retrain that wrote one but not the
        # other is drift, and loads refuse it.
        meta_cols = _model_meta.get("feature_cols")
        if meta_cols is not None and list(meta_cols) != list(_feature_cols):
            raise FeatureDriftError(
                "meta.json feature_cols disagree with features.pkl "
                f"({len(meta_cols)} vs {len(_feature_cols)} columns)"
            )
        # Hazard model optional
        try:
            _hazard_model = joblib.load(REPO_ROOT / "models" / "carry_hazard" / "hazard.pkl")
            _hazard_scaler = joblib.load(REPO_ROOT / "models" / "carry_hazard" / "hazard_scaler.pkl")
        except FileNotFoundError:
            pass
        _loaded = True
        logger.info("Carry models loaded successfully")
    except FileNotFoundError as e:
        # Absent artifacts are not an empty model: zeros would masquerade as
        # a real q20=0 prediction (a default inside the value domain). Raise
        # so callers degrade explicitly instead of trading on nothing.
        raise FeatureDriftError(f"carry model artifacts absent: {e}") from e


def _load_model_meta() -> dict:
    """Best-effort training sidecar. Absent (all pre-sidecar artifacts) or
    malformed meta is NOT fatal — age simply goes unassessed (the 4.6
    doctrine: unknown age is neither fresh nor expired). A present,
    well-formed sidecar is returned for schema + staleness checks."""
    path = MODELS_DIR / "meta.json"
    if not path.exists():
        return {}
    try:
        import json
        meta = json.loads(path.read_text())
        return meta if isinstance(meta, dict) else {}
    except Exception as e:  # noqa: BLE001 — corrupt sidecar degrades to absent
        logger.warning(f"Ignoring malformed model meta.json: {e}")
        return {}


def get_model_metadata() -> dict:
    """Training sidecar for the loaded artifacts ({} when absent/pre-sidecar)."""
    if not _loaded:
        _load_models()
    return dict(_model_meta)


def _require_loaded_width(features: np.ndarray) -> None:
    """Fail-closed shape gate: raw-array callers bypass prepare_, so the
    width check lives here too. A right-count/wrong-order array is still the
    caller's responsibility (names can't travel with ndarrays); a wrong
    COUNT is always drift."""
    if _feature_cols is None:
        raise FeatureDriftError("feature schema not loaded — refusing to predict")
    if features.ndim != 2 or features.shape[1] != len(_feature_cols):
        raise FeatureDriftError(
            f"feature width {features.shape} != schema width {len(_feature_cols)} "
            f"({_feature_cols})"
        )


def do_predict(
    features: np.ndarray,
    feature_bounds: tuple | None = None,
    model_trained_at: float | None = None,
    max_model_age_s: float | None = None,
) -> np.ndarray:
    """Per-row novelty gate: int flag per input row — 1 ok, 0 shield,
    2 expired model. Strategy must gate entry on flag == 1.

    - Non-finite values (NaN/inf) always shield: the row is outside anything
      the model was fit on. (4.5 lets NaN through prepare_ because LightGBM
      is NaN-native; entry on NaN is a separate, stricter question — this.)
    - feature_bounds (lo, hi) arrays aligned to schema order, when supplied,
      shield out-of-range rows. No training distribution stats ship with
      the artifacts yet (that sidecar is 4.9); without bounds only
      finiteness gates, which is documented, not faked.
    - model_trained_at + max_model_age_s, when both supplied, mark every
      row expired (2) past the retraining window. No timestamp is inferred
      from file mtime (checkout noise lies); absent timestamps mean age is
      unknown, never "fresh".
    Shape drift (wrong width) raises FeatureDriftError — a caller bug, not
    a novel row."""
    if not _loaded:
        _load_models()
    arr = np.asarray(features, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    _require_loaded_width(arr)
    if model_trained_at is not None and max_model_age_s is not None:
        import time
        if time.time() - model_trained_at > max_model_age_s:
            return np.full(arr.shape[0], 2, dtype=int)
    flags = np.ones(arr.shape[0], dtype=int)
    finite = np.isfinite(arr).all(axis=1)
    flags[~finite] = 0
    if feature_bounds is not None:
        lo, hi = (np.asarray(b, dtype=np.float64) for b in feature_bounds)
        in_bounds = ((arr >= lo) & (arr <= hi)).all(axis=1)
        flags[~in_bounds & (flags == 1)] = 0
    return flags


def predict_q20(features: np.ndarray) -> np.ndarray:
    """Predict q20 (20% quantile) for y_sum_7d_bps."""
    if not _loaded:
        _load_models()
    if _q20_model is None:
        raise FeatureDriftError("q20 model missing after load — refusing zero-fill")
    # Ensure 2D input
    if features.ndim == 1:
        features = features.reshape(1, -1)
    _require_loaded_width(features)
    return _q20_model.predict(features)


def predict_q50(features: np.ndarray) -> np.ndarray:
    """Predict q50 (median) for y_sum_7d_bps."""
    if not _loaded:
        _load_models()
    if _q50_model is None:
        raise FeatureDriftError("q50 model missing after load — refusing zero-fill")
    if features.ndim == 1:
        features = features.reshape(1, -1)
    _require_loaded_width(features)
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


# The persisted artifact (models/carry_quantile/features.pkl, written by
# ml/train_carry.py from its FEATURE_COLS) is the single source of truth
# for the inference schema — there is intentionally no second list here.
# (A stale 22-column copy used to live in this module while the artifact
# carried 26; the duplication is exactly the drift this gate exists to
# stop, so the dead list was deleted, not synced.)

def prepare_features_for_inference(row_dict: dict) -> np.ndarray:
    """
    Convert a single row dict to feature array for inference.
    Expected keys are the persisted training schema (_feature_cols).

    Missing keys are schema drift and RAISE FeatureDriftError — a partial
    row (e.g. the 6 keys the live signal path used to pass against a
    26-column model) must never be zero-filled into a prediction. NaN
    values pass through untouched: LightGBM handles NaN natively, and
    zero-filling them would destroy that handling to fake completeness.
    """
    if not _loaded:
        _load_models()
    if _feature_cols is None:
        raise FeatureDriftError("feature schema not loaded — refusing to predict")
    if not isinstance(row_dict, dict):
        raise FeatureDriftError(
            f"feature row must be a dict keyed by column name, "
            f"got {type(row_dict).__name__}"
        )

    missing = [c for c in _feature_cols if c not in row_dict or row_dict[c] is None]
    if missing:
        raise FeatureDriftError(
            f"row is missing {len(missing)}/{len(_feature_cols)} model features "
            f"(schema drift, refusing zero-fill): {missing}"
        )
    # Build row in schema order
    row = []
    for col in _feature_cols:
        val = row_dict[col]
        if isinstance(val, bool):
            raise FeatureDriftError(
                f"feature {col!r} is bool (True-as-1 coercion refused)"
            )
        try:
            row.append(float(val))  # NaN passes through: native to LightGBM
        except (TypeError, ValueError):
            raise FeatureDriftError(
                f"feature {col!r} is non-numeric ({val!r})"
            ) from None
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
    max_model_age_s: float | None = None,
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
        "novelty_flag": int,  # 4.6 per-row gate
        "shielded": bool,
    }

    Model staleness (4.8): the training timestamp comes from the meta.json
    sidecar when present; expiry defaults to MODEL_MAX_AGE_S and is
    overridable per call. No sidecar (pre-sidecar artifacts) means age is
    unknown — assessed as neither fresh nor expired, per the 4.6 doctrine.
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
    # Novelty shield (4.6): a shielded row never reaches the model — the
    # verdict is "no trustworthy prediction", not a model output. eligible
    # is forced False; callers degrade on `shielded` (S5), not on q20.
    # Staleness (4.8) feeds the same shield: an expired model expires
    # every row it would have priced.
    trained_at = _model_meta.get("trained_at")
    # Sidecar absent -> trained_at None -> age unassessed (never expires).
    age_cap = None
    if trained_at is not None:
        age_cap = MODEL_MAX_AGE_S if max_model_age_s is None else max_model_age_s
    novelty_flag = int(do_predict(
        feat, model_trained_at=trained_at, max_model_age_s=age_cap)[0])
    shielded = novelty_flag != 1
    if shielded:
        return {
            "q20_bps": float("nan"),
            "q50_bps": float("nan"),
            "eligible": False,
            "pin_state": pin_state,
            "survival_prob": 1.0,
            "expected_pnl": 0.0,
            "novelty_flag": novelty_flag,
            "shielded": True,
        }
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
        "novelty_flag": novelty_flag,
        "shielded": False,
    }


# ============================================================
# For backward compatibility with signals.py
# ============================================================
class CarryDecision:
    def __init__(self, will_clear: bool, confidence: float, raw_answer: str,
                 shielded: bool = False, novelty_flag: int = 1):
        self.will_clear = will_clear
        self.confidence = confidence
        self.raw_answer = raw_answer
        # Novelty-gate verdict travels with the decision so strategies gate
        # entry on it instead of re-deriving it. Defaults keep third-party
        # constructions (tests, scripts) unshielded-explicit.
        self.shielded = shielded
        self.novelty_flag = novelty_flag


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
    shielded = result.get("shielded", False)
    q20 = result["q20_bps"]
    # A shielded row carries NaN q20 (no prediction was made): confidence 0
    # and an explicit label, never a formatted NaN masquerading as a number.
    return CarryDecision(
        will_clear=result["eligible"] and not shielded,
        confidence=0.0 if shielded else q20 / 10000.0,  # rough proxy
        raw_answer="q20=shielded" if shielded else f"q20={q20:.1f} bps",
        shielded=shielded,
        novelty_flag=result.get("novelty_flag", 1),
    )


if __name__ == "__main__":
    # Test loading
    _load_models()
    print("Models loaded:", _q20_model is not None, _q50_model is not None)