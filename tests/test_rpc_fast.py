"""TODO 7.1: RPC Fast sidetrack — unconfigured keeps the OKX path, errors
fall back with typed counting, the status surface is honest-null."""
import base64
import struct

import pytest

from src import solana_rpc as rpc
from src.solana_rpc import (
    PYTH_MAGIC,
    RpcFastError,
    _parse_pyth_price,
    configured,
    fetch_solana_market_snapshot,
    rpc_fast_status,
)


@pytest.fixture()
def clean_env(monkeypatch):
    for k in ("RPC_FAST_URL", "RPC_FAST_GRPC_URL", "RPC_FAST_API_KEY",
              "RPC_FAST_PLAN", "RPC_FAST_TIMEOUT_SECONDS"):
        monkeypatch.delenv(k, raising=False)
    from src.settings import refresh_settings
    refresh_settings()
    yield
    refresh_settings()


def _pyth_account(price: int, expo: int) -> str:
    raw = bytearray(168)
    struct.pack_into("<I", raw, 0, PYTH_MAGIC)
    struct.pack_into("<i", raw, 20, expo)
    struct.pack_into("<q", raw, 160, price)
    return base64.b64encode(bytes(raw)).decode()


def test_unconfigured_raises_and_okx_path_untouched(clean_env):
    assert configured() is False
    with pytest.raises(RpcFastError):
        fetch_solana_market_snapshot()
    status = rpc_fast_status()
    assert status["configured"] is False
    assert status["plan"] is None and status["latency_s"] is None


def test_pyth_parse_and_magic_gate():
    # $200.00 as 20000 x 10^-2.
    assert _parse_pyth_price(_pyth_account(20000, -2)) == pytest.approx(200.0)
    bad = bytearray(base64.b64decode(_pyth_account(20000, -2)))
    struct.pack_into("<I", bad, 0, 0xDEADBEEF)
    with pytest.raises(RpcFastError):
        _parse_pyth_price(base64.b64encode(bytes(bad)).decode())
    with pytest.raises(RpcFastError):
        _parse_pyth_price("e30=")  # valid b64, too short


def test_fetch_success_counts_and_shapes(monkeypatch, clean_env):
    from src import metrics
    metrics.reset()
    monkeypatch.setenv("RPC_FAST_URL", "https://fast.example/rpc")
    monkeypatch.setenv("RPC_FAST_PLAN", "starter")
    from src.settings import refresh_settings
    refresh_settings()

    calls = []

    def fake_post(url, payload, timeout):
        calls.append(payload["method"])
        if payload["method"] == "getSlot":
            return {"result": 300_000_000}
        return {"result": {"value": {"data": [
            _pyth_account(21050, -2), "base64"]}}}

    monkeypatch.setattr(rpc, "_post", fake_post)
    out = fetch_solana_market_snapshot()
    assert out["source"] == "rpc-fast"
    assert out["sol_usd"] == pytest.approx(210.50)
    assert out["slot"] == 300_000_000
    assert calls == ["getSlot", "getAccountInfo"]
    snap = metrics.snapshot()["counters"]
    key = 'tars_rpc_fast_usage_total{result="ok",source="rpc-fast"}'
    assert snap.get(key, 0) == 1
    status = rpc_fast_status()
    assert status["configured"] is True and status["plan"] == "starter"


def test_fetch_error_falls_back_with_count(monkeypatch, clean_env):
    from src import metrics
    metrics.reset()
    monkeypatch.setenv("RPC_FAST_URL", "https://fast.example/rpc")
    from src.settings import refresh_settings
    refresh_settings()

    def boom(url, payload, timeout):
        raise ConnectionError("down")

    monkeypatch.setattr(rpc, "_post", boom)
    with pytest.raises(RpcFastError):
        fetch_solana_market_snapshot()
    snap = metrics.snapshot()["counters"]
    key = 'tars_rpc_fast_usage_total{result="error",source="rpc-fast"}'
    assert snap.get(key, 0) == 1


def test_status_route_honest_null(clean_env):
    from fastapi.testclient import TestClient
    from src.main import app
    r = TestClient(app).get("/api/v1/rpc-fast-status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["grpc"] == "not-implemented"


def _clean_solami(monkeypatch):
    for k in ("SOLAMI_RPC_URL", "SOLAMI_API_KEY", "SOLAMI_PLAN"):
        monkeypatch.delenv(k, raising=False)
    from src.settings import refresh_settings
    refresh_settings()


def test_solami_unconfigured_raises(clean_env, monkeypatch):
    from src import solana_rpc as rpc
    from src.settings import refresh_settings
    _clean_solami(monkeypatch)
    assert rpc.solami_configured() is False
    with pytest.raises(rpc.RpcFastError):
        rpc.fetch_via_solami()
    refresh_settings()


def test_solami_fetch_counts_source(monkeypatch, clean_env):
    from src import metrics
    from src import solana_rpc as rpc
    import base64
    import struct
    _clean_solami(monkeypatch)
    metrics.reset()
    monkeypatch.setenv("SOLAMI_RPC_URL", "https://edge.solami.dev/rpc")
    from src.settings import refresh_settings
    refresh_settings()
    assert rpc.solami_configured() is True

    raw = bytearray(168)
    struct.pack_into("<I", raw, 0, rpc.PYTH_MAGIC)
    struct.pack_into("<i", raw, 20, -2)
    struct.pack_into("<q", raw, 160, 21050)
    blob = base64.b64encode(bytes(raw)).decode()

    def fake_post(url, payload, timeout):
        assert url == "https://edge.solami.dev/rpc"
        if payload["method"] == "getSlot":
            return {"result": 300_000_001}
        return {"result": {"value": {"data": [blob, "base64"]}}}

    monkeypatch.setattr(rpc, "_post", fake_post)
    out = rpc.fetch_via_solami()
    assert out["source"] == "solami"
    assert out["sol_usd"] == pytest.approx(210.50)
    snap = metrics.snapshot()["counters"]
    key = 'tars_rpc_fast_usage_total{result="ok",source="solami"}'
    assert snap.get(key, 0) == 1
    refresh_settings()


def test_solami_key_assembles_official_shape(monkeypatch, clean_env):
    """No explicit URL: the key assembles the documented Edge shape."""
    from src import solana_rpc as rpc
    from src.settings import refresh_settings
    _clean_solami(monkeypatch)
    monkeypatch.setenv("SOLAMI_API_KEY", "k123")
    refresh_settings()
    assert rpc.solami_configured() is True

    seen = {}

    def fake_post(url, payload, timeout):
        seen["url"] = url
        if payload["method"] == "getSlot":
            return {"result": 1}
        raise rpc.RpcFastError("stop after slot")

    monkeypatch.setattr(rpc, "_post", fake_post)
    with pytest.raises(rpc.RpcFastError):
        rpc.fetch_via_solami()
    assert seen["url"] == "https://rpc.solami.fast/sol?api_key=k123"
    refresh_settings()


def test_solami_explicit_url_wins_over_key(monkeypatch, clean_env):
    from src import solana_rpc as rpc
    from src.settings import refresh_settings
    _clean_solami(monkeypatch)
    monkeypatch.setenv("SOLAMI_API_KEY", "k123")
    monkeypatch.setenv("SOLAMI_RPC_URL", "https://fra.rpc.solami.dev/sol?api_key=k123")
    refresh_settings()
    assert rpc._resolve_solami_url(refresh_settings()) == \
        "https://fra.rpc.solami.dev/sol?api_key=k123"
    refresh_settings()
