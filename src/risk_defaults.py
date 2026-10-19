"""Single source of truth for risk constants (S9).

Every duplicated risk default used to live in two or three places
(RiskGate.__init__, Settings, multi_leg dispatch), so changing one
silently diverged the others. Values here == long-standing production
behavior; changing a number here is a deliberate policy change, not a
refactor.

Stdlib only, on purpose: both the execution layer and the settings layer
import this module, so it must never pull heavy dependencies (or either
import chain breaks).

UNITS (the trap this table exists to close): the gate speaks PERCENT
(max_slippage_pct 1.0 = 1%) while multi_leg steps speak FRACTION
(0.003 = 0.3%). Convert only through the helpers below — never inline.
"""
from __future__ import annotations

import math

RISK_DEFAULTS = {
    "max_position_usd": 5000.0,
    "max_daily_loss_usd": 500.0,
    "max_daily_trades": 10,
    "max_daily_volume_usd": 50000.0,
    "max_leverage": 5.0,
    "min_confidence_bps": 7000,
    "max_price_age_seconds": 60.0,
    "max_slippage_pct": 1.0,  # gate units: PERCENT
    "max_reference_velocity_pct_per_s": 0.1,  # gate units: PERCENT/s
    "loss_cooldown_minutes": 30.0,
    "drawdown_window_days": 3,
    "drawdown_loss_mult": 2.0,
    "regime_band_pct": 5.0,
    "regime_size_scale": 0.8,
}


def slippage_pct_to_fraction(pct: float) -> float:
    """Gate-percent (1.0 = 1%) -> dispatch fraction (0.01). Strict:
    bool/stringified/NaN/negative never convert (2.7 coercion discipline)."""
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        raise ValueError(
            f"slippage pct must be a number, got {pct!r} "
            f"(type {type(pct).__name__})"
        )
    if not math.isfinite(float(pct)) or float(pct) < 0:
        raise ValueError(f"slippage pct must be finite and >= 0, got {pct!r}")
    return float(pct) / 100.0


def slippage_fraction_to_pct(frac: float) -> float:
    """Dispatch fraction (0.01) -> gate-percent (1.0 = 1%). Same strictness."""
    if isinstance(frac, bool) or not isinstance(frac, (int, float)):
        raise ValueError(
            f"slippage fraction must be a number, got {frac!r} "
            f"(type {type(frac).__name__})"
        )
    if not math.isfinite(float(frac)) or float(frac) < 0:
        raise ValueError(f"slippage fraction must be finite and >= 0, got {frac!r}")
    return float(frac) * 100.0
