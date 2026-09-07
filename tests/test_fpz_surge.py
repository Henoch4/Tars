"""Unit tests for the funding_persistence_z surge detector (bored2boar steal).

Volatility burst → persistence window widens 5 → 7 (pickier entries during
bursts). Disabled by default; legacy behavior must be byte-identical when
off or in calm.

Run: pytest tests/test_fpz_surge.py -v
"""
import pytest

from src.signals import funding_persistence_z_signal

# Funding tape: 16 inside the mild band + 5 hot. At window 5 persistence
# is 1.0 (fires); at window 7 it is 5/7 (blocked). z ≈ 1.8 clears the
# 1.5 bar either way.
HOT_FUNDING = [0.0001] * 16 + [0.004] * 5
HOT_RATE = 0.004

# Price tapes (>= 2*24+1 = 49 points for the baseline comparison).
CALM_PRICES = [100.0] * 60
BURST_PRICES = (
    [100.0 + (0.1 if i % 2 else -0.1) for i in range(49)]
    + [100.0] * 16
    + [100.0 + (4.0 if i % 2 else -4.0) for i in range(8)]
)


def test_burst_with_surge_widens_window_and_blocks():
    sig = funding_persistence_z_signal(
        "SOL-USDT-SWAP", HOT_RATE,
        funding_history=list(HOT_FUNDING), price_history=list(BURST_PRICES),
        surge_enabled=True,
    )
    assert sig.metadata["surge_detected"] is True
    assert sig.metadata["vol_ratio"] > 2.0
    assert sig.metadata["persist_window"] == 7
    assert sig.direction == "NEUTRAL"  # 5/7 persistence < 0.8
    assert "surge" in sig.rationale


def test_burst_without_surge_fires_legacy():
    sig = funding_persistence_z_signal(
        "SOL-USDT-SWAP", HOT_RATE,
        funding_history=list(HOT_FUNDING), price_history=list(BURST_PRICES),
    )
    assert sig.direction == "SHORT"
    assert sig.confidence_bps >= 7000
    assert sig.metadata["surge_detected"] is False
    assert sig.metadata["persist_window"] == 5


def test_calm_with_surge_enabled_matches_legacy():
    enabled = funding_persistence_z_signal(
        "SOL-USDT-SWAP", HOT_RATE,
        funding_history=list(HOT_FUNDING), price_history=list(CALM_PRICES),
        surge_enabled=True,
    )
    legacy = funding_persistence_z_signal(
        "SOL-USDT-SWAP", HOT_RATE,
        funding_history=list(HOT_FUNDING), price_history=list(CALM_PRICES),
    )
    assert enabled.metadata["surge_detected"] is False
    assert enabled.direction == legacy.direction == "SHORT"
    assert enabled.confidence_bps == legacy.confidence_bps
    assert enabled.metadata["persistence"] == legacy.metadata["persistence"]


def test_short_price_history_abstains_from_surge():
    sig = funding_persistence_z_signal(
        "SOL-USDT-SWAP", HOT_RATE,
        funding_history=list(HOT_FUNDING), price_history=[100.0] * 30,
        surge_enabled=True,
    )
    # No baseline available → no surge → legacy window-5 outcome.
    assert sig.metadata["surge_detected"] is False
    assert sig.metadata["persist_window"] == 5
    assert sig.direction == "SHORT"


def test_no_price_history_is_legacy():
    sig = funding_persistence_z_signal(
        "SOL-USDT-SWAP", HOT_RATE,
        funding_history=list(HOT_FUNDING), price_history=None,
        surge_enabled=True,
    )
    assert sig.metadata["surge_detected"] is False
    assert sig.direction == "SHORT"
