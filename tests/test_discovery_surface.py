"""5.8 verification: full discovery surface served and coherent.

Agent-card + x402 shapes are pinned in test_manifest.py (TestAgentDiscovery).
This pins the complete I4 set: /.well-known/agent-card.json, /.well-known/x402,
/docs, /openapi.json — and that the card's entry points resolve to live routes
(discover -> price card -> /hire with no human).
"""
import pytest


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import src.main as main
    return TestClient(main.app)


def test_discovery_surface_all_200(client):
    for path in ("/.well-known/agent-card.json", "/.well-known/x402",
                 "/docs", "/openapi.json"):
        r = client.get(path)
        assert r.status_code == 200, path


def test_card_entry_points_resolve(client):
    card = client.get("/.well-known/agent-card.json").json()
    for name, path in card["entry_points"].items():
        if not isinstance(path, str) or not path.startswith("/"):
            continue
        r = client.get(path)
        assert r.status_code in (200, 401, 402, 403, 405), (name, path,
                                                            r.status_code)


def test_openapi_covers_hire(client):
    spec = client.get("/openapi.json").json()
    assert "/hire" in spec["paths"]


def test_registry_pointer_honest_null_when_undeployed(client, monkeypatch):
    import os
    monkeypatch.delenv("REGISTRY_CONTRACT_ADDRESS", raising=False)
    card = client.get("/.well-known/agent-card.json").json()
    assert card["registry"]["contract"] is None
