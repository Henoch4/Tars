"""S6 pin + Z4 audit pins (Phase 1 item 7).

S6: per-asset guards `continue`, never `return` — verified the loop in
run_trading_cycle already does this (gather(return_exceptions=True) +
per-asset continue); this file pins the behavior so a later refactor
cannot silently reintroduce batch-abort.

Z4 audit outcome: no code change required. backtest_simple compounds
capital on NET pnl, classifies wins on NET, and headlines NET
(total_return_bps); gross_return_bps is reported alongside for cost-drag
visibility and nothing in src/ consumes it as a decision input. No
lockedProfit-style nominal figure and no gross fallback exist anywhere
in the profit path (agent total_pnl_usd is an unwired 0.0 dead metric,
not a phantom profit — out of scope). These tests pin the net-driven
reporting so it cannot regress.
"""
import pytest

from src.signals import Signal, backtest_simple


def _longs(n: int) -> list[Signal]:
    return [
        Signal(strategy="t", asset="BTC-USDT-SWAP", direction="LONG",
               confidence_bps=8000, rationale="t")
        for _ in range(n)
    ]


class TestZ4NetDrivenReporting:
    def test_headline_is_net_of_costs(self):
        """With real costs, the headline return must sit BELOW gross by
        the cost drag — costs actually deducted, not displayed alongside
        a gross headline."""
        prices = [100.0 + i for i in range(20)]  # steady climb, all longs win gross
        res = backtest_simple(prices, _longs(19), initial_capital=10000,
                              fee_bps=5.0, slippage_bps=3.0)
        assert res.num_trades > 0
        assert res.total_costs_usd > 0
        assert res.gross_return_bps > 0
        assert res.total_return_bps < res.gross_return_bps

    def test_zero_cost_run_is_documented_gross_run(self):
        """Zero costs => headline equals gross. The docstring says a
        zero-cost run IS a gross run; pin that equivalence so nobody
        mistakes a costless backtest for edge evidence."""
        prices = [100.0 + i for i in range(20)]
        res = backtest_simple(prices, _longs(19), initial_capital=10000)
        assert res.num_trades > 0
        assert res.total_costs_usd == 0
        assert res.total_return_bps == res.gross_return_bps

    def test_wins_classified_on_net_not_gross(self):
        """A trade that wins gross but loses net of costs counts as a loss."""
        # One trade: +10bps gross move, costs 2*(5+3)=16bps round-trip.
        prices = [100.0, 100.01]
        res = backtest_simple(prices, _longs(1), initial_capital=10000,
                              fee_bps=5.0, slippage_bps=3.0)
        assert res.num_trades == 1
        assert res.gross_return_bps > 0
        assert res.total_return_bps < 0
        assert res.win_rate == 0.0


@pytest.mark.asyncio
async def test_sibling_asset_survives_per_asset_failure(monkeypatch):
    """S6 pin: one asset raising inside its pipeline must not abort its
    siblings — the error is recorded, the sibling still produces signals."""
    from src.agent import AutonomousTradingAgent
    from src.execution import RiskGate

    class _StubCli:
        async def run(self, *args, **kwargs):
            cmd = args[:1]
            if cmd == ("market",) and "trades" in args:
                return {"data": [{"px": "100", "sz": "1"} for _ in range(30)]}
            if cmd == ("market",) and "funding-rate" in args:
                return {"data": [{"fundingRate": "0.00001"}]}
            return {"data": []}

    gate = RiskGate(
        max_position_usd=5000,
        allowed_assets=["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        allowed_companions=[],
    )
    agent = AutonomousTradingAgent(okx_cli=_StubCli(), risk_gate=gate,
                                   dry_run=True)

    orig_generate = agent._generate_signals

    def flaky(asset, md, spot_price=None):
        if asset == "ETH-USDT-SWAP":
            raise RuntimeError("simulated per-asset signal failure")
        return orig_generate(asset, md, spot_price)

    monkeypatch.setattr(agent, "_generate_signals", flaky)
    result = await agent.run_trading_cycle(["BTC-USDT-SWAP", "ETH-USDT-SWAP"])

    assert any("simulated per-asset signal failure" in e for e in result.errors)
    btc = [s for s in result.signals if s["asset"] == "BTC-USDT-SWAP"]
    assert len(btc) == 1  # sibling completed despite the failure
