"""SafetyNet unit-contract regressions.

Carry helpers return a rate per dollar of position notional; the risk hurdle is
in dollars. These tests pin the conversion so a realistic carry signal is not
silently rejected by comparing unlike units.
"""
from __future__ import annotations

import pytest

from src.safety_net import safety_net
from src.signals import Signal


def _signal(funding_rate: float, *, vol: float = 0.01) -> Signal:
    return Signal(
        strategy="funding_persistence_z",
        asset="SOL-USDT-SWAP",
        direction="SHORT",
        confidence_bps=9200,
        entry_price=100.0,
        rationale="test carry signal",
        metadata={"funding_rate": funding_rate, "vol": vol},
    )


def test_realistic_carry_is_approved_after_notional_conversion():
    result = safety_net(_signal(0.004), capital=50_000.0)

    assert result.approved
    assert result.size_usd == pytest.approx(33_333.333333333336)
    assert result.expected_pnl is not None
    assert result.expected_pnl > 50_000.0 * 0.01 * 1.5


def test_uneconomic_carry_is_rejected_in_usd_units():
    result = safety_net(_signal(0.0001), capital=50_000.0)

    assert not result.approved
    assert result.expected_pnl is not None
    assert result.expected_pnl < 0.0
    assert "E[PnL] $" in result.reason
    assert "R*RR $750.00" in result.reason


def test_stop_distance_changes_notional_and_expected_dollars():
    low_vol = safety_net(_signal(0.0015, vol=0.01), capital=50_000.0)
    high_vol = safety_net(_signal(0.0015, vol=0.02), capital=50_000.0)

    assert low_vol.size_usd == pytest.approx(33_333.333333333336)
    assert low_vol.expected_pnl is not None
    assert high_vol.expected_pnl is not None
    assert low_vol.expected_pnl == pytest.approx(high_vol.expected_pnl * (4.0 / 3.0))
