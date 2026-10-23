"""I3/I6 regression: pricing is declared once, enforced from the same
table, and callers can see the price before paying.

Pins: tier vocabulary, the price card, the estimate endpoint (known and
unknown routes), the over-cap fail-closed rule, per-tier bucket behavior
(including fail-closed on unknown tiers and per-IP independence), and
that the card's paid flags come from the table while enforcement stays
off without credentials.
"""
import pytest


def _client():
    from fastapi.testclient import TestClient
    import src.main as main
    return TestClient(main.app)


class TestPriceCard:
    def test_card_shape(self):
        body = _client().get("/api/v1/pricing").json()
        assert set(body["tiers"]) == {"free", "micro", "premium"}
        assert body["cap_usdc"] == "5.00"
        assert body["enforced"] is False  # no PAY_TO_ADDRESS in test env
        by_path = {r["route"]: r for r in body["routes"]}
        assert by_path["/hire"]["tier"] == "premium"
        assert by_path["/hire"]["price_usdc"] == "0.50"
        assert by_path["/positions"]["tier"] == "micro"
        # Unlisted routes are free and therefore absent from the card.
        assert "/health" not in by_path

    def test_estimate_known_route(self):
        body = _client().get("/api/v1/estimate", params={"route": "/hire"}).json()
        assert body == {"route": "/hire", "tier": "premium",
                        "price_usdc": "0.50", "enforced": False,
                        "cap_usdc": "5.00"}

    def test_estimate_unknown_route_is_free(self):
        body = _client().get("/api/v1/estimate", params={"route": "/nope"}).json()
        assert body["tier"] == "free"
        assert body["price_usdc"] == "0.00"


class TestCapFailClosed:
    def test_over_cap_route_left_ungated(self, monkeypatch):
        import src.main as main
        monkeypatch.setitem(main.PRICED_ROUTES, "/expensive",
                            {"tier": "premium", "price_usdc": "9.99",
                             "description": "x"})
        try:
            built = main._build_paid_routes()
        finally:
            del main.PRICED_ROUTES["/expensive"]
        assert "/expensive" not in built

    def test_lowered_cap_skips_existing_prices(self, monkeypatch):
        import src.main as main
        monkeypatch.setattr(main, "X402_MAX_USD_PER_CALL", 0.10)
        built = main._build_paid_routes()
        assert "/hire" not in built  # 0.50 > 0.10 cap

    def test_non_numeric_price_skipped(self, monkeypatch):
        import src.main as main
        monkeypatch.setitem(main.PRICED_ROUTES, "/broken",
                            {"tier": "premium", "price_usdc": "free!",
                             "description": "x"})
        try:
            built = main._build_paid_routes()
        finally:
            del main.PRICED_ROUTES["/broken"]
        assert "/broken" not in built


class TestTierBuckets:
    def test_unknown_tier_fails_closed(self):
        import src.main as main
        assert main._check_tier_limit("1.2.3.4", "ultra") is False

    def test_bucket_trips_and_per_ip_independent(self, monkeypatch):
        import src.main as main
        monkeypatch.setitem(main._TIER_PER_MIN, "premium", 2)
        assert main._check_tier_limit("10.0.0.1", "premium") is True
        assert main._check_tier_limit("10.0.0.1", "premium") is True
        assert main._check_tier_limit("10.0.0.1", "premium") is False
        assert main._check_tier_limit("10.0.0.2", "premium") is True


class TestCardEnforcementSplit:
    def test_paid_flags_from_table_enforcement_off(self):
        card = _client().get("/.well-known/agent-card.json").json()
        assert card["payment"]["enabled"] is False
        by_path = {t["path"]: t for t in card["tools"]}
        # Model advertised...
        assert by_path["/trade"]["paid"] is True
        assert by_path["/trade"]["tier"] == "premium"
        # ...while nothing is enforced.
        x402 = _client().get("/.well-known/x402").json()
        assert x402["enabled"] is False
        assert x402["paid_routes"] == []

    def test_every_tool_says_whether_it_is_actually_charged(self):
        """`paid` (listed at a price) and `enforced` (gated now) must both be
        readable, or a priced route answering 200 looks like a billing bug."""
        card = _client().get("/.well-known/agent-card.json").json()
        tools = {t["path"]: t for t in card["tools"]}
        for tool in card["tools"]:
            assert isinstance(tool["enforced"], bool)
        assert tools["/hire"]["paid"] is True
        assert tools["/hire"]["enforced"] is False  # no PAY_TO_ADDRESS
        assert tools["/health"]["paid"] is False
        assert tools["/health"]["enforced"] is False

    def test_card_and_well_known_list_prices_and_posture(self):
        card = _client().get("/.well-known/agent-card.json").json()
        payment = card["payment"]
        assert payment["listed_routes"]["/hire"] == "0.50"
        assert payment["enforced_routes"] == []
        assert payment["posture"].startswith("launch-free")

        x402 = _client().get("/.well-known/x402").json()
        assert x402["listed_routes"]["/hire"] == "0.50"
        assert "PAY_TO_ADDRESS" in x402["how_to_enable"]


class TestGatingWhenPayToConfigured:
    def test_payee_gates_hire_on_xlayer(self, monkeypatch):
        import types

        import src.main as main
        payee = "0x" + "11" * 20
        monkeypatch.setattr(main, "settings",
                            types.SimpleNamespace(pay_to_address=payee))
        built = main._build_paid_routes()
        accept = built["/hire"]["accepts"][0]
        assert accept["pay_to"] == payee
        assert accept["price"] == "$0.50"
        assert accept["network"] == "eip155:196"
        assert accept["scheme"] == "exact"
        # Every under-cap priced route is gated; nothing unpriced leaks in.
        assert set(built) == set(main.PRICED_ROUTES)
