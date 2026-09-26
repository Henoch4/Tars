"""API tests for rails status + Injective funding (no network on success path)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

import src.main as main

client = TestClient(main.app, raise_server_exceptions=False)


def test_rails_shape_and_no_secrets():
    r = client.get("/api/v1/rails")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"exchange", "evm", "solana", "injective"}
    assert set(body["evm"]) >= {"configured", "chain_id"}
    assert set(body["solana"]) >= {"enabled", "rpc_url", "program_id", "explorer"}
    assert set(body["injective"]) >= {"testnet", "grpc", "chain_id", "markets", "key_configured"}
    blob = json.dumps(body)
    for banned in ("PRIVATE_KEY", "AGENT_WALLET", "MOOVE_API_KEY", "OKX_SECRET",
                   "BINANCE_API_SECRET", "PASSPHRASE"):
        assert banned not in blob
    # Solana disabled by default in this env
    assert body["solana"]["enabled"] is False
    assert body["solana"]["program_id"] is None


def test_injective_funding_unknown_asset_is_502():
    r = client.get("/api/v1/injective/funding", params={"asset": "NOPE-USDT-SWAP"})
    assert r.status_code == 502
    assert "market" in r.json()["detail"].lower()


def test_injective_funding_unreachable_grpc_is_502():
    os.environ["INJECTIVE_GRPC"] = "127.0.0.1:1"
    try:
        r = client.get("/api/v1/injective/funding", params={"asset": "BTC-USDT-SWAP"})
    finally:
        os.environ.pop("INJECTIVE_GRPC", None)
    assert r.status_code == 502
