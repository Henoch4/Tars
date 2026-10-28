"""Cost model regression tests.

Tests the CostModel dataclass and derived functions:
- round_trip_cost_bps
- per_period_cost_bps
- break_even_funding_rate

Validates defaults, validation of invalid inputs, and consistency
between cost model and carry_break_even_rate.
"""
import pytest

from src.cost_model import (
    BOOK_1_COST_MODEL,
    CostModel,
    break_even_funding_rate,
    per_period_cost_bps,
    round_trip_cost_bps,
)
from src.signals import Signal, carry_break_even_rate, backtest_simple


class TestCostModelDefaults:
    def test_default_values_match_book1(self):
        """Defaults match the repo's documented Book 1 assumptions."""
        cm = CostModel()
        assert cm.taker_fee_bps_spot == 5.0
        assert cm.taker_fee_bps_perp == 5.0
        assert cm.slippage_bps_per_leg == 3.0
        assert cm.hold_periods == 21.0
        assert cm.ev_margin == 2.0

    def test_book1_constant_matches_default(self):
        """BOOK_1_COST_MODEL equals a default-constructed CostModel."""
        assert BOOK_1_COST_MODEL == CostModel()


class TestRoundTripCostBps:
    def test_default_round_trip_is_32bps(self):
        # 2 * (5 + 5 + 2*3) = 32 bps
        assert round_trip_cost_bps(BOOK_1_COST_MODEL) == pytest.approx(32.0)

    def test_zero_fees_zero_cost(self):
        cm = CostModel(
            taker_fee_bps_spot=0.0,
            taker_fee_bps_perp=0.0,
            slippage_bps_per_leg=0.0,
        )
        assert round_trip_cost_bps(cm) == 0.0

    def test_custom_values(self):
        cm = CostModel(
            taker_fee_bps_spot=10.0,
            taker_fee_bps_perp=10.0,
            slippage_bps_per_leg=5.0,
        )
        # 2 * (10 + 10 + 2*5) = 2 * 30 = 60 bps
        assert round_trip_cost_bps(cm) == pytest.approx(60.0)

    def test_negative_fee_raises(self):
        with pytest.raises(ValueError):
            CostModel(taker_fee_bps_spot=-1.0)

        with pytest.raises(ValueError):
            CostModel(taker_fee_bps_perp=-1.0)

        with pytest.raises(ValueError):
            CostModel(slippage_bps_per_leg=-1.0)


class TestPerPeriodCostBps:
    def test_default_per_period_cost(self):
        # 32 bps / 21 periods = ~1.5238 bps per period
        expected = 32.0 / 21.0
        assert per_period_cost_bps(BOOK_1_COST_MODEL) == pytest.approx(expected)

    def test_custom_hold_periods(self):
        cm = CostModel(hold_periods=7.0)
        # 32 bps / 7 periods = ~4.5714 bps per period
        assert per_period_cost_bps(cm) == pytest.approx(32.0 / 7.0)

    def test_zero_hold_raises(self):
        with pytest.raises(ValueError):
            CostModel(hold_periods=0)


class TestBreakEvenFundingRate:
    def test_default_break_even_matches_carry_break_even_rate(self):
        cm = CostModel()
        expected = carry_break_even_rate(
            taker_fee_bps_spot=cm.taker_fee_bps_spot,
            taker_fee_bps_perp=cm.taker_fee_bps_perp,
            slippage_bps_per_leg=cm.slippage_bps_per_leg,
            hold_periods=cm.hold_periods,
        )
        assert break_even_funding_rate(cm) == pytest.approx(expected)

    def test_higher_fees_raise_break_even(self):
        cm_low = CostModel(
            taker_fee_bps_spot=5.0,
            taker_fee_bps_perp=5.0,
            slippage_bps_per_leg=3.0,
        )
        cm_high = CostModel(
            taker_fee_bps_spot=50.0,
            taker_fee_bps_perp=50.0,
            slippage_bps_per_leg=3.0,
        )
        assert break_even_funding_rate(cm_high) > break_even_funding_rate(cm_low)

    def test_shorter_hold_raises_break_even(self):
        cm_short = CostModel(hold_periods=7.0)
        cm_long = CostModel(hold_periods=21.0)
        assert break_even_funding_rate(cm_short) > break_even_funding_rate(cm_long)

    def test_survival_prob_scales_up(self):
        cm = CostModel()
        be_low_survival = break_even_funding_rate(cm, survival_prob=0.5)
        be_high_survival = break_even_funding_rate(cm, survival_prob=1.0)
        assert be_low_survival > be_high_survival

    def test_pin_state_arg_accepted(self):
        cm = CostModel()
        # pin_state != 0 should be accepted (informational only)
        be = break_even_funding_rate(cm, pin_state=1)
        assert be == break_even_funding_rate(cm, pin_state=0)


class TestBreakEvenRespectsEvMargin:
    def test_higher_ev_margin_raises_break_even(self):
        cm_low_margin = CostModel(ev_margin=1.0)
        cm_high_margin = CostModel(ev_margin=2.0)
        assert break_even_funding_rate(cm_high_margin) > break_even_funding_rate(cm_low_margin)

    def test_ev_margin_of_1_halves_break_even_vs_2(self):
        # Break-even scales linearly with ev_margin.
        cm_1x = CostModel(ev_margin=1.0)
        cm_2x = CostModel(ev_margin=2.0)
        assert break_even_funding_rate(cm_2x) == pytest.approx(2.0 * break_even_funding_rate(cm_1x))


class TestBacktestCostModelIntegration:
    def test_default_cost_model_charges_full_round_trip_cost(self):
        """When cost_model is provided, per-trade cost must equal the full
        round-trip cost (32 bps under defaults), not a partial amount."""
        from src.signals import backtest_simple

        prices = [100.0, 110.0]
        signals = [Signal("test", "BTC", "LONG", 8000, 100.0)]
        result = backtest_simple(
            prices, signals, initial_capital=10000,
            cost_model=BOOK_1_COST_MODEL,
        )
        # Round-trip cost = 32 bps of 10000 notional = $3.20 = 32 bps of capital
        expected_costs = 10000 * 32.0 / 10000
        assert result.total_costs_usd == pytest.approx(expected_costs)
        # Net = gross (1000 bps) - costs (32 bps) = 968 bps
        assert result.total_return_bps == pytest.approx(1000 - 32)

    def test_backtest_cost_model_charges_once_not_twice(self):
        """Without cost_model, fee_bps/slippage_bps are charged separately.
        With cost_model, the full round-trip cost (32 bps) should be charged
        once, not double-counted."""
        from src.signals import backtest_simple

        prices = [100.0, 110.0]
        signals = [Signal("test", "BTC", "LONG", 8000, 100.0)]

        # Without cost_model: fee_bps=5 + slippage_bps=3 per side per leg
        # cost_rate = 2*(5+3) = 16 bps per trade (undercharged vs true 32 bps)
        without_cm = backtest_simple(prices, signals, initial_capital=10000,
                                     fee_bps=5, slippage_bps=3)
        assert without_cm.total_costs_usd == pytest.approx(10000 * 16.0 / 10000)

        # With cost_model: full round-trip cost = 32 bps
        with_cm = backtest_simple(prices, signals, initial_capital=10000,
                                  cost_model=BOOK_1_COST_MODEL)
        assert with_cm.total_costs_usd == pytest.approx(10000 * 32.0 / 10000)

        # The cost_model version should charge exactly 2x the without-cm version
        assert with_cm.total_costs_usd == pytest.approx(2 * without_cm.total_costs_usd)
