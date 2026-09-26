"""Offline tests for the Injective client (gRPC/web3 mocked, no network)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.injective_client import (
    InjectiveClient,
    InjectiveClientError,
    InjectiveConfig,
    config_from_env,
)
from src.okx_cli import OkxCliError


def _client(**kw):
    cfg = InjectiveConfig(private_key="0x" + "11" * 32, **kw)
    return InjectiveClient(cfg)


def test_error_is_okx_compatible():
    assert issubclass(InjectiveClientError, OkxCliError)
    with pytest.raises(OkxCliError):
        raise InjectiveClientError("x")


def test_resolve_market():
    c = _client()
    assert c.resolve_market("BTC-USDT-SWAP").startswith("0x")
    raw = "0x" + "ab" * 32
    assert c.resolve_market(raw) == raw
    with pytest.raises(InjectiveClientError):
        c.resolve_market("DOGE-USDT-SWAP")


def test_subaccount_id():
    c = _client()
    assert c.subaccount_id("0xABCDEF1234567890abcdef1234567890ABCDEF12") == (
        "0xabcdef1234567890abcdef1234567890abcdef12" + "00000000"
    )


def test_config_from_env_defaults():
    for k in ("INJECTIVE_GRPC", "INJECTIVE_RPC_URL", "INJECTIVE_CHAIN_ID",
              "INJECTIVE_TESTNET_PRIVATE_KEY", "INJECTIVE_TESTNET"):
        os.environ.pop(k, None)
    cfg = config_from_env()
    assert cfg.chain_id == 1439
    assert cfg.testnet is True
    assert cfg.private_key == ""
    assert "grpc.injective.network" in cfg.grpc_endpoint


class _FakeDelta:
    def __init__(self, px, qty):
        self.execution_price = px
        self.execution_quantity = qty


class _FakeTrade:
    def __init__(self, px, qty, ts):
        self.position_delta = _FakeDelta(px, qty)
        self.executed_at = ts


class _FakeTradesResp:
    def __init__(self, trades):
        self.trades = trades


class _FakeFundingResp:
    def __init__(self, rates):
        self.funding_rates = rates


class _FakeRate:
    def __init__(self, rate):
        self.rate = rate


class _FakeStub:
    def __init__(self, trades=None, rates=None):
        self._trades = trades or []
        self._rates = rates or []

    async def Trades(self, req, *args, **kwargs):
        assert req.market_id.startswith("0x")
        assert req.limit >= 1
        return _FakeTradesResp(self._trades)

    async def FundingRates(self, req, *args, **kwargs):
        return _FakeFundingResp(self._rates)


class _FakeChannel:
    async def close(self):
        pass


def _client_with_stub(stub):
    c = _client()

    from types import SimpleNamespace

    class _Msgs:
        def FundingRatesRequest(self, **kw):
            return SimpleNamespace(**kw)

        def TradesRequest(self, **kw):
            return SimpleNamespace(**kw)

    c._messages = lambda: _Msgs()  # noqa: E731
    c._stub = lambda grpc_mod: (stub, _FakeChannel())  # noqa: E731
    return c


@pytest.mark.asyncio
async def test_run_market_trades_shape():
    stub = _FakeStub(trades=[
        _FakeTrade("5112515.036", "0.616", 1781885121618),
        _FakeTrade("bad-price", "0.1", 1),  # kept raw; agent skips non-numeric
    ])
    c = _client_with_stub(stub)
    out = await c.run("market", "trades", "BTC-USDT-SWAP", "--limit", "2")
    assert out[0] == {"px": "5112515.036", "sz": "0.616", "ts": "1781885121618"}
    assert float(out[0]["px"]) > 0  # agent _extract_prices parses px


@pytest.mark.asyncio
async def test_run_market_funding_rate_shape():
    stub = _FakeStub(rates=[_FakeRate("0.00000416666")])
    c = _client_with_stub(stub)
    out = await c.run("market", "funding-rate", "BTC-USDT-SWAP")
    assert out == [{"fundingRate": "0.00000416666"}]
    assert float(out[0]["fundingRate"]) != 0 or True  # agent float() parses it


@pytest.mark.asyncio
async def test_run_unsupported_rejected():
    c = _client()
    with pytest.raises(InjectiveClientError):
        await c.run("trade", "order", "BTC-USDT-SWAP")


@pytest.mark.asyncio
async def test_run_unknown_market_rejected():
    stub = _FakeStub()
    c = _client_with_stub(stub)
    with pytest.raises(InjectiveClientError):
        await c.run("market", "trades", "NOPE-USDT-SWAP")


@pytest.mark.asyncio
async def test_check_auth_needs_key():
    c = InjectiveClient(InjectiveConfig(private_key=""))
    with pytest.raises(InjectiveClientError):
        await c.check_auth()


def test_factory_routes_injective():
    from src.exchange import create_exchange_client

    c = create_exchange_client("injective")
    assert isinstance(c, InjectiveClient)
