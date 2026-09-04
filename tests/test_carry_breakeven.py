"""Z1 regression: fee-aware carry break-even, not a flat threshold.

A flat funding threshold is wrong in both directions: too loose when fees
are high or the hold is short (money-losing books slip through), too
strict when costs are low (profitable carry refused). The package gate
requires funding above BOTH the flat floor and the computed break-even —
the binding bound is their max, so the gate only ever tightens.
"""
import pytest

from src.signals import CARRY_EV_MARGIN, carry_break_even_rate


class TestBreakEvenMath:
    def test_defaults_match_hand_computation(self):
        # 2 legs * (5 + 5 + 2*3)bps = 32bps, 2x margin, 21 periods.
        assert carry_break_even_rate() == pytest.approx(32 / 10000 * 2.0 / 21)

    def test_higher_fees_raise_break_even(self):
        assert (carry_break_even_rate(taker_fee_bps_spot=50.0)
                > carry_break_even_rate())

    def test_shorter_hold_raises_break_even(self):
        assert (carry_break_even_rate(hold_periods=7.0)
                > carry_break_even_rate(hold_periods=21.0))

    def test_zero_hold_raises(self):
        with pytest.raises(ValueError):
            carry_break_even_rate(hold_periods=0)

    def test_negative_fee_raises(self):
        with pytest.raises(ValueError):
            carry_break_even_rate(taker_fee_bps_perp=-1.0)

    def test_margin_constant_documents_dataset_link(self):
        # Same 2x-costs bar as EV_MARGIN in scripts/build_carry_dataset.py.
        assert CARRY_EV_MARGIN == 2.0


def _gate_agent(**overrides):
    from src.agent import AutonomousTradingAgent
    from src.execution import RiskGate
    from src.multi_leg import MultiLegExecutionManager

    class _StubCli:
        async def run(self, *args, **kwargs):
            return {"data": []}

    gate = RiskGate(
        max_position_usd=5000,
        allowed_assets=["BTC-USDT-SWAP"],
        allowed_companions=["BTC-USDT"],
    )
    kwargs = dict(
        okx_cli=_StubCli(),
        risk_gate=gate,
        dry_run=True,
        multi_leg_manager=MultiLegExecutionManager(),
    )
    kwargs.update(overrides)
    return AutonomousTradingAgent(**kwargs)


class TestGateUsesMaxOfFloorAndBreakEven:
    def test_defaults_floor_binds(self):
        # Defaults: break-even ~0.0003 < floor 0.001 — behavior unchanged.
        agent = _gate_agent()
        assert agent._funding_arb_opportunity(
            "BTC-USDT-SWAP", {"funding_rate": 0.005}, 100.0, 100.1) is True
        assert agent._funding_arb_opportunity(
            "BTC-USDT-SWAP", {"funding_rate": 0.0005}, 100.0, 100.1) is False

    def test_high_fees_block_despite_clearing_floor(self):
        """0.0015 clears the 0.001 floor but not a 50bps-fee break-even
        (~0.0020) — the package that would lose money is refused."""
        agent = _gate_agent(carry_taker_fee_bps_spot=50.0,
                            carry_taker_fee_bps_perp=50.0)
        assert agent._funding_arb_opportunity(
            "BTC-USDT-SWAP", {"funding_rate": 0.0015}, 100.0, 100.1) is False

    def test_rich_funding_clears_high_fee_break_even(self):
        agent = _gate_agent(carry_taker_fee_bps_spot=50.0,
                            carry_taker_fee_bps_perp=50.0)
        assert agent._funding_arb_opportunity(
            "BTC-USDT-SWAP", {"funding_rate": 0.005}, 100.0, 100.1) is True

    def test_short_hold_tightens_gate(self):
        """Same funding, shorter expected hold -> break-even binds -> blocked."""
        agent = _gate_agent(carry_hold_periods=3.0)
        # break-even = 32bps*2/3 ≈ 0.00213 > 0.0015 funding
        assert agent._funding_arb_opportunity(
            "BTC-USDT-SWAP", {"funding_rate": 0.0015}, 100.0, 100.1) is False
