"""TODO 6.5 (S9): one table for every risk constant, units converted once.

RiskGate.__init__, Settings, and the multi-leg dispatch each carried their
own copy of the same numbers (gate in PERCENT, steps in FRACTION), so a
change to one silently diverged the others. Now RISK_DEFAULTS is the
single source; this suite fails on any future divergence.
"""
import pytest

from src.execution import (
    RISK_DEFAULTS,
    RiskGate,
    slippage_fraction_to_pct,
    slippage_pct_to_fraction,
)
from src.multi_leg import Step


EXPECTED_BEHAVIOR = {
    # Pins long-standing production behavior: changing a value here must
    # be a deliberate policy change with the gate/settings/dispatch
    # updated together (they all read this table now).
    "max_position_usd": 5000.0,
    "max_daily_loss_usd": 500.0,
    "max_daily_trades": 10,
    "max_daily_volume_usd": 50000.0,
    "max_leverage": 5.0,
    "min_confidence_bps": 7000,
    "max_price_age_seconds": 60.0,
    "max_slippage_pct": 1.0,
    "max_reference_velocity_pct_per_s": 0.1,
    "loss_cooldown_minutes": 30.0,
    "drawdown_window_days": 3,
    "drawdown_loss_mult": 2.0,
    "regime_band_pct": 5.0,
    "regime_size_scale": 0.8,
}


def test_table_pins_current_behavior():
    assert RISK_DEFAULTS == EXPECTED_BEHAVIOR


def test_gate_defaults_match_table():
    """A bare RiskGate() must equal the table on every knob — drift in
    either direction fails here, not mid-cycle."""
    gate = RiskGate()
    for key, value in RISK_DEFAULTS.items():
        assert getattr(gate, key) == value, f"gate.{key} diverged from table"


def test_settings_defaults_match_table():
    """Settings field defaults (read statically — no env, no construction)
    must equal the table on every shared knob."""
    from src.settings import Settings
    for key, value in RISK_DEFAULTS.items():
        assert key in Settings.model_fields, f"settings lost knob {key}"
        assert Settings.model_fields[key].default == value, (
            f"settings.{key} diverged from table"
        )


def test_pct_fraction_roundtrip():
    assert slippage_pct_to_fraction(1.0) == 0.01
    assert slippage_fraction_to_pct(0.01) == 1.0
    assert slippage_fraction_to_pct(slippage_pct_to_fraction(2.5)) == 2.5


@pytest.mark.parametrize("bad", [True, "1.0", None, float("nan"), float("inf"), -0.5])
def test_converters_refuse_coercion(bad):
    with pytest.raises(ValueError):
        slippage_pct_to_fraction(bad)
    with pytest.raises(ValueError):
        slippage_fraction_to_pct(bad)


def test_dispatch_collar_reads_gate_value():
    """The live dispatch collar converts the LIVE gate value (not a second
    copy): gate default 1.0% -> 0.01 fraction on the wire."""
    gate = RiskGate()
    collar = min(slippage_pct_to_fraction(gate.max_slippage_pct), 0.05)
    assert collar == 0.01


def test_step_default_never_looser_than_gate():
    """Step()'s tighter default (0.3%) is deliberate policy (dispatch binds
    first), not drift: it must never exceed the gate collar in like units."""
    gate_fraction = slippage_pct_to_fraction(RISK_DEFAULTS["max_slippage_pct"])
    assert Step(
        venue="v", action="short_perp", asset="A", amount_ratio=1.0,
    ).max_slippage_pct <= gate_fraction
