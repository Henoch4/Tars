"""Port tests: Stockulus fee schedules + HMM selector + venue-aware costs."""
import pytest

from src.meme_venue_costs import (
    SCHEDULES,
    hmm_state_for_market,
    meteora_leg_cost_bps,
    satellite_round_trip_cost_usd,
    select_fee_schedule,
)
from src.meme_satellite import PaperBlotter, PaperPosition


def test_schedules_match_stockulus_configs():
    assert SCHEDULES["calm"]["fee_start_bps"] == 120.0
    assert SCHEDULES["calm"]["regime_scale"] == 1.0
    assert SCHEDULES["stress"]["fee_start_bps"] == 900.0
    assert SCHEDULES["stress"]["regime_scale"] == 0.7


def test_state_selects_schedule_and_mult():
    assert select_fee_schedule(0)["fee_mult"] == 1.0
    assert select_fee_schedule(0)["curve"] == "linear"
    assert select_fee_schedule(1)["fee_mult"] == 1.2
    assert select_fee_schedule(2)["curve"] == "exponential"
    assert select_fee_schedule(2)["fee_mult"] == 2.0
    with pytest.raises(ValueError):
        select_fee_schedule(3)
    with pytest.raises(ValueError):
        select_fee_schedule("calm")


def test_hmm_states_follow_thresholds():
    assert hmm_state_for_market(0.1, 0.5, 10.0, 0.01) == 0
    assert hmm_state_for_market(0.5, 0.5, 10.0, 0.01) == 1
    assert hmm_state_for_market(0.9, 0.5, 10.0, 0.01) == 2
    with pytest.raises(ValueError):
        hmm_state_for_market("high", 0.5, 10.0, 0.01)
    with pytest.raises(ValueError):
        hmm_state_for_market(0.1, float("nan"), 10.0, 0.01)


def test_venue_cost_is_fee_plus_impact():
    # $1k into $100k pool, calm: 120bps fee + 100bps impact = 220bps/leg.
    sched = select_fee_schedule(0)
    assert meteora_leg_cost_bps(1000.0, 100_000.0, sched) == pytest.approx(220.0)
    # Stress doubles the fee leg, impact unchanged.
    stress = meteora_leg_cost_bps(1000.0, 100_000.0, select_fee_schedule(2))
    assert stress == pytest.approx(900.0 * 2.0 + 100.0)


def test_unpriceable_liquidity_raises():
    sched = select_fee_schedule(0)
    with pytest.raises(ValueError):
        meteora_leg_cost_bps(1000.0, 0.0, sched)
    with pytest.raises(ValueError):
        meteora_leg_cost_bps(-50.0, 100_000.0, sched)


def test_round_trip_breakdown_shape():
    out = satellite_round_trip_cost_usd(1000.0, 100_000.0, 0)
    assert out["schedule"] == "calm"
    assert out["round_trip_usd"] == pytest.approx(1000.0 * 0.022 * 2)


def test_blotter_accepts_venue_slippage():
    blotter = PaperBlotter()
    pos = PaperPosition(mint="M", entry_usd=100.0, entry_price=1.0,
                        peak_price=1.0, entry_ts=0.0,
                        entry_window_volume_usd=10_000.0, size_usd=100.0)
    rec = blotter.record_exit(pos, 1.1, 3600.0, "test", slippage_bps=2.2)
    # +10 gross, 2.2bps x2 sides + gas.
    assert rec["costs_usd"] == pytest.approx(100.0 * 0.022 * 2 + 0.5)
    # Default path unchanged (flat 1% proxy).
    rec2 = blotter.record_exit(pos, 1.1, 3600.0, "test")
    assert rec2["costs_usd"] == pytest.approx(100.0 * 0.01 * 2 + 0.5)
