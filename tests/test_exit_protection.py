import pytest

from src.agent import AutonomousTradingAgent
from src.execution import ExecutionError, OrderExecutor, OrderRequest
from src.signals import Signal


def _signal(direction: str, price: float = 100.0) -> Signal:
    return Signal(
        strategy="test",
        asset="BTC-USDT-SWAP",
        direction=direction,
        confidence_bps=8000,
        entry_price=price,
    )


def test_long_exit_prices_are_attached_atomically():
    order = OrderRequest("BTC-USDT-SWAP", "buy", "market", "100")
    AutonomousTradingAgent._attach_exit_protection(order, _signal("LONG"), 0.01, 0.015)

    assert order.stop_trigger_px == "99"
    assert order.take_trigger_px == "101.5"
    assert order.exit_protection_required is True
    assert order.to_dict()["slOrdPx"] == "-1"
    assert order.to_dict()["tpOrdPx"] == "-1"


def test_short_exit_prices_reverse_stop_and_take():
    order = OrderRequest("BTC-USDT-SWAP", "sell", "market", "100")
    AutonomousTradingAgent._attach_exit_protection(order, _signal("SHORT"), 0.01, 0.015)

    assert order.stop_trigger_px == "101"
    assert order.take_trigger_px == "98.5"


def test_invalid_exit_inputs_fail_closed():
    order = OrderRequest("BTC-USDT-SWAP", "buy", "market", "100")
    with pytest.raises(ValueError):
        AutonomousTradingAgent._attach_exit_protection(order, _signal("LONG"), None, 0.015)


@pytest.mark.asyncio
async def test_required_exit_protection_rejects_naked_executor_order():
    order = OrderRequest("BTC-USDT-SWAP", "buy", "market", "100", exit_protection_required=True)
    executor = OrderExecutor(cli=None, risk_gate=None, dry_run=True)
    with pytest.raises(ExecutionError, match="unprotected entry"):
        await executor.place_order(order)
