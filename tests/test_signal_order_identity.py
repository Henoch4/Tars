"""Regression for fpz spec §12.1: invalid generated OKX clOrdId.

The old signal_<strategy>_<8 hex> was 37 characters for fpz and also
contained underscores. Exercise real order construction and execution,
without network calls, credentials, or a SafetyNet stub.
"""
import re
import time

import pytest

from src.agent import AutonomousTradingAgent
from src.execution import OrderExecutor, RiskGate
from src.signals import Signal


class _NoNetworkCli:
    async def run(self, *args, **kwargs):
        raise AssertionError("identity tests must not call the exchange")


def _order(strategy="funding_persistence_z", direction="SHORT"):
    agent = AutonomousTradingAgent(
        okx_cli=_NoNetworkCli(), risk_gate=RiskGate(), dry_run=True,
    )
    signal = Signal(
        strategy=strategy, asset="SOL-USDT-SWAP", direction=direction,
        confidence_bps=9200, entry_price=150.0, rationale="identity regression",
    )
    return agent._signal_to_order(signal, {"position": None})


@pytest.mark.parametrize("strategy", [
    "funding_persistence_z", "ml_funding_carry", "mean_reversion",
    "funding_rate", "x" * 256, "strategy_with-punctuation/and spaces",
    "非ASCII", "",
])
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_signal_order_id_meets_okx_contract(strategy, direction):
    order = _order(strategy, direction)
    assert order is not None
    assert re.fullmatch(r"[A-Za-z0-9]{1,32}", order.client_oid)
    assert order.to_dict()["clOrdId"] == order.client_oid
    assert order.reduce_only is False
    assert order.intended_price == 150.0


def test_repeated_signals_get_distinct_ids_without_rewriting_existing_order():
    first = _order()
    second = _order()
    assert first.client_oid != second.client_oid
    original = first.client_oid
    assert first.to_dict()["clOrdId"] == original
    assert first.to_dict()["clOrdId"] == original


@pytest.mark.asyncio
async def test_paper_execution_preserves_signal_order_identity():
    order = _order()
    original = order.client_oid
    executor = OrderExecutor(cli=_NoNetworkCli(), risk_gate=RiskGate(), dry_run=True)
    result = await executor.place_order(
        order, current_price=150.0, current_price_timestamp=time.time(),
    )
    assert result.client_oid == original == order.client_oid
    assert result.raw["clOrdId"] == original
