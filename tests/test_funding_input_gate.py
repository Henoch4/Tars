"""Missing funding is distinct from a valid zero funding rate."""
from __future__ import annotations

import pytest

from src.agent import AutonomousTradingAgent
from src.data_integrity import DataIntegrityGate


class _FakeCli:
    def __init__(self, funding, trades=None):
        self.funding = funding
        self.trades = [] if trades is None else trades

    async def run(self, group, command, asset, *args, **kwargs):
        if command == "trades":
            return self.trades
        if command == "funding-rate":
            return self.funding
        raise AssertionError(command)


class _FakeExecutor:
    async def get_position_snapshot(self, asset):
        return None


@pytest.mark.asyncio
async def test_process_asset_skips_when_funding_read_failed():
    agent = object.__new__(AutonomousTradingAgent)
    out = await agent._process_asset(
        {"asset": "SOL-USDT-SWAP", "funding_rate": 0.0,
         "funding_available": False}, "cycle-test", None
    )
    assert out["signals"] == []
    assert "unavailable" in out["errors"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    [{"fundingRate": "not-a-number"}],
    [{"fundingRate": "nan"}],
    {"data": [{"fundingRate": "inf"}]},
    {"data": []},
])
async def test_fetch_market_data_marks_malformed_funding_unavailable(payload):
    agent = object.__new__(AutonomousTradingAgent)
    agent.cli = _FakeCli(payload)
    agent.executor = _FakeExecutor()

    out = await agent._fetch_market_data("SOL-USDT-SWAP", lookback=1)

    assert out["funding_rate"] == 0.0
    assert out["funding_available"] is False


@pytest.mark.parametrize("raw, expected", [
    (1_700_000_000, 1_700_000_000),
    (1_700_000_000_000, 1_700_000_000),
    ("not-a-timestamp", None),
    (float("nan"), None),
])
def test_exchange_timestamp_normalization_is_strict(raw, expected):
    assert AutonomousTradingAgent._normalize_exchange_timestamp(raw) == expected


@pytest.mark.asyncio
async def test_fetch_market_data_uses_exchange_timestamp_not_local_fetch_time():
    agent = object.__new__(AutonomousTradingAgent)
    agent.cli = _FakeCli(
        [{"fundingRate": "0.0001"}],
        trades=[{"ts": "1700000000000", "px": "100"}],
    )
    agent.executor = _FakeExecutor()

    out = await agent._fetch_market_data("SOL-USDT-SWAP", lookback=1)

    assert out["timestamp"] == 1_700_000_000
    assert out["timestamp_available"] is True


@pytest.mark.asyncio
async def test_fetch_market_data_marks_missing_exchange_timestamp_unavailable():
    agent = object.__new__(AutonomousTradingAgent)
    agent.cli = _FakeCli(
        [{"fundingRate": "0.0001"}],
        trades=[{"px": "100"}],
    )
    agent.executor = _FakeExecutor()

    out = await agent._fetch_market_data("SOL-USDT-SWAP", lookback=1)

    assert out["timestamp_available"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("trades", [
    [],
    [{"ts": "1700000000000", "px": "not-a-price"}],
    [{"ts": "1700000000000", "px": "nan"}],
    [{"ts": "1700000000000", "px": "0"}],
])
async def test_fetch_market_data_marks_invalid_prices_unavailable(trades):
    agent = object.__new__(AutonomousTradingAgent)
    agent.cli = _FakeCli(
        [{"fundingRate": "0.0001"}],
        trades=trades,
    )
    agent.executor = _FakeExecutor()

    out = await agent._fetch_market_data("SOL-USDT-SWAP", lookback=1)

    assert out["price_available"] is False


@pytest.mark.asyncio
async def test_fetch_market_data_marks_valid_price_available():
    agent = object.__new__(AutonomousTradingAgent)
    agent.cli = _FakeCli(
        [{"fundingRate": "0.0001"}],
        trades=[{"ts": "1700000000000", "px": "100"}],
    )
    agent.executor = _FakeExecutor()

    out = await agent._fetch_market_data("SOL-USDT-SWAP", lookback=1)

    assert out["price_available"] is True


def test_unavailable_exchange_timestamp_blocks_integrity_gate():
    agent = object.__new__(AutonomousTradingAgent)
    agent.integrity_gate = DataIntegrityGate()

    result = agent._check_integrity(
        "SOL-USDT-SWAP",
        {"timestamp_available": False, "funding_rate": 0.0001},
    )

    assert result is not None
    assert result.blocks_trading
    assert "timestamp unavailable" in result.reasons[0]


def test_unavailable_market_price_blocks_integrity_gate():
    agent = object.__new__(AutonomousTradingAgent)
    agent.integrity_gate = DataIntegrityGate()

    result = agent._check_integrity(
        "SOL-USDT-SWAP",
        {"price_available": False, "funding_rate": 0.0001},
    )

    assert result is not None
    assert result.blocks_trading
    assert "price unavailable" in result.reasons[0]
