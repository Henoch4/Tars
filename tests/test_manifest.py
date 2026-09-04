"""I7 regression: manifest.json validates and matches the live routes.

Pins the doctor gate in-process: schema shape, no placeholder
descriptions, no mojibake, and bidirectional route agreement so the
manifest can never drift from what /manifest actually serves to
okx.ai registration.
"""
import json
import pathlib

from scripts.check_manifest import check_manifest


def _manifest():
    return json.loads(pathlib.Path("manifest.json").read_text(encoding="utf-8"))


class TestManifestDoctor:
    def test_doctor_reports_healthy(self):
        assert check_manifest() == []

    def test_required_keys_present(self):
        m = _manifest()
        assert {"name", "version", "endpoints", "capabilities"} <= set(m)

    def test_every_endpoint_documented(self):
        for ep in _manifest()["endpoints"]:
            assert ep["path"] and ep["method"] and ep["description"]
            assert ep["description"] != "unknown endpoint"
            assert not ep["description"].endswith(" endpoint") or \
                " " not in ep["description"].removesuffix(" endpoint")

    def test_manifest_matches_live_routes(self):
        from src.main import app
        live = set()
        for route in app.routes:
            methods = sorted(getattr(route, "methods", None) or ())
            if not methods or getattr(route, "include_in_schema", True) is False:
                continue
            for meth in methods:
                if meth != "HEAD":
                    live.add((route.path, meth))
        manifest_keys = {(e["path"], e["method"]) for e in _manifest()["endpoints"]}
        assert manifest_keys == live

    def test_metrics_endpoint_registered(self):
        keys = {(e["path"], e["method"]) for e in _manifest()["endpoints"]}
        assert ("/api/v1/metrics", "GET") in keys
        assert ("/health", "GET") in keys


class TestAgentDiscovery:
    """I4: another AI agent can discover TARS, read the tool catalog and
    payment posture, and find the entry points — without a human."""

    def _client(self):
        from fastapi.testclient import TestClient
        import src.main as main
        return TestClient(main.app)

    def test_agent_card_shape(self):
        card = self._client().get("/.well-known/agent-card.json")
        assert card.status_code == 200
        body = card.json()
        for key in ("name", "description", "version", "chain",
                    "capabilities", "entry_points", "payment", "tools"):
            assert key in body, f"agent card missing {key}"
        assert body["entry_points"]["hire"] == "/hire"
        assert body["entry_points"]["docs"] == "/docs"

    def test_agent_card_tools_match_manifest(self):
        card = self._client().get("/.well-known/agent-card.json").json()
        manifest_tools = {(e["path"], e["method"])
                          for e in _manifest()["endpoints"]}
        card_tools = {(t["path"], t["method"]) for t in card["tools"]}
        assert card_tools == manifest_tools
        assert all(t["description"] for t in card["tools"])

    def test_agent_card_payment_honest_when_paywall_off(self):
        # Phase 1: PAY_TO_ADDRESS unset -> enforcement disabled, but the
        # PRICE MODEL is still advertised (paid = priced in the table).
        # The card must never claim enforcement that doesn't exist.
        card = self._client().get("/.well-known/agent-card.json").json()
        assert card["payment"]["protocol"] == "x402"
        assert card["payment"]["enabled"] is False
        by_path = {t["path"]: t for t in card["tools"]}
        assert by_path["/hire"]["paid"] is True
        assert by_path["/hire"]["price_usdc"] == "0.50"
        assert by_path["/health"]["paid"] is False
        assert by_path["/health"]["price_usdc"] == "0.00"

    def test_x402_well_known_shape(self):
        body = self._client().get("/.well-known/x402")
        assert body.status_code == 200
        data = body.json()
        assert data["protocol"] == "x402"
        assert data["enabled"] is False
        assert data["paid_routes"] == []
        assert data["network"] == "eip155:196"
