"""OKX position snapshot adapter regressions."""
from __future__ import annotations

import pytest

from src.execution.position_reader import OkxPositionReader, PositionReadError
from src.execution.executor import OrderExecutor
from src.execution.models import ExecutionError
from src.multi_leg import Step


class FakeCli:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.result


def step():
    return Step("okx", "short_perp", "SOL", 1.0)


@pytest.mark.asyncio
async def test_okx_reader_maps_instrument_and_sums_positions():
    cli = FakeCli({"data": [{"pos": "2.5"}, {"pos": "-0.5"}]})
    reader = OkxPositionReader(cli, lambda _: "SOL-USDT-SWAP", lambda s: "okx:SOL-USDT-SWAP")
    assert await reader(step()) == pytest.approx(2.0)
    assert "SOL-USDT-SWAP" in cli.calls[0][0]


@pytest.mark.asyncio
async def test_okx_reader_distinguishes_empty_from_malformed():
    empty = OkxPositionReader(FakeCli({"data": []}), lambda _: "SOL-USDT-SWAP", lambda s: "x")
    assert await empty(step()) == 0.0
    malformed = OkxPositionReader(FakeCli({"oops": 1}), lambda _: "SOL-USDT-SWAP", lambda s: "x")
    with pytest.raises(PositionReadError):
        await malformed(step())


@pytest.mark.asyncio
async def test_okx_reader_fails_closed_on_transport_error():
    reader = OkxPositionReader(FakeCli(error=TimeoutError("offline")), lambda _: "SOL-USDT-SWAP", lambda s: "x")
    with pytest.raises(PositionReadError):
        await reader(step())


@pytest.mark.asyncio
async def test_executor_strict_position_read_distinguishes_empty_and_failure():
    empty = OrderExecutor(FakeCli({"data": []}), object(), dry_run=True)
    assert await empty.get_position_snapshot("SOL-USDT-SWAP") is None
    broken = OrderExecutor(FakeCli({"bad": 1}), object(), dry_run=True)
    with pytest.raises(ExecutionError):
        await broken.get_position_snapshot("SOL-USDT-SWAP")
