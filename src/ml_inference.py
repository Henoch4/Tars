"""
Backward compatibility layer for ML inference.
Re-exports from ml.carry_inference for backward compatibility with tests.
"""
from ml.carry_inference import (
    predict_carry_decision as _predict_carry_decision_impl,
    CarryDecision,
    predict_q20,
    predict_q50,
    is_quantile_pass,
    quantile_coverage_check,
    hazard_predict_proba,
    survival_curve_from_hazards,
    expected_carry_pnl,
    prepare_features_for_inference,
)

def predict_carry_clear(features):
    """Wrapper for backward compatibility - calls internal implementation and returns CarryDecision."""
    result = _predict_carry_decision_impl(features)
    return CarryDecision(
        will_clear=result["eligible"],
        confidence=result["q20_bps"] / 10000.0,
        raw_answer=f"q20={result['q20_bps']:.1f} bps"
    )

def predict_carry_decision(features, premium_history=None, interest_rate=0.0001, q20_threshold_bps=16.0):
    """Calls predict_carry_clear and converts CarryDecision to dict for backward compatibility."""
    decision = predict_carry_clear(features)
    return {
        "eligible": decision.will_clear,
        "q20_bps": decision.confidence * 10000.0,
        "q50_bps": 0.0,
        "pin_state": 0,
        "survival_prob": 1.0,
        "expected_pnl": 0.0,
    }

def get_tars_lora_client():
    """Returns a mock client for testing, or None if not available."""
    # In production, this would return a real tars-lora client.
    # For testing, we return a mock object that has the necessary methods.
    class _MockTarsClient:
        def predict(self, features):
            # This would be called by the ML model in production
            raise NotImplementedError("Mock client - not implemented")

    return _MockTarsClient()

__all__ = [
    "predict_carry_clear",
    "predict_carry_decision",
    "CarryDecision",
    "predict_q20",
    "predict_q50",
    "is_quantile_pass",
    "quantile_coverage_check",
    "hazard_predict_proba",
    "survival_curve_from_hazards",
    "expected_carry_pnl",
    "prepare_features_for_inference",
    "get_tars_lora_client",
]