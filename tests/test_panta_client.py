"""Panta client: documented paths/fields only (verified 2026-10-09
against https://docs.panta.market). MockTransport throughout — no network,
no keys."""
import json

import httpx
import pytest

from src.panta_client import PantaClient, PantaError

pytestmark = pytest.mark.asyncio


def _client(handler, api_key="pk_test_x"):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport,
                             base_url="https://live-api.panta.market/api/v1")
    return PantaClient(api_key=api_key, http_client=http)


def _ok(payload):
    return httpx.Response(200, json=payload)


async def test_catalog_uses_list_path_and_cursor():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return _ok({"items": [], "nextCursor": None})

    c = _client(handler)
    await c.catalog(limit=20, category="crypto", status="primary")
    assert "/markets/" in seen["url"]
    assert "catalog" not in seen["url"]
    assert "category=crypto" in seen["url"] and "status=primary" in seen["url"]
    await c.close()


async def test_market_detail_path():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return _ok({"marketId": "m"})

    c = _client(handler)
    await c.market_detail("EventPda111")
    assert seen["url"].endswith("/markets/EventPda111/")
    await c.close()


async def test_buy_quote_sends_wallet_side_amount():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content.decode())
        return _ok({"quoteId": "qt_1"})

    c = _client(handler)
    out = await c.primary_buy_quote(market_id="EventPda111", wallet="Buyer111",
                                    side="YES", amount_usdc="20.00")
    assert out["quoteId"] == "qt_1"
    assert seen["body"] == {"wallet": "Buyer111", "marketId": "EventPda111",
                            "side": "yes", "amountUsdc": "20.00"}
    await c.close()


async def test_buy_build_and_submit_paths():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return _ok({"ok": True})

    c = _client(handler)
    await c.primary_buy_build(quote_id="qt_1", wallet="Buyer111")
    await c.primary_buy_submit(order_id="ord_1", signature="sig1")
    assert "/primaryorderbuild/" in seen[0]
    assert "/primaryordersubmit/" in seen[1]
    await c.close()


async def test_positions_uses_query_param():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return _ok({"positions": []})

    c = _client(handler)
    await c.wallet_positions("Buyer111")
    assert "/positions/" in seen["url"] and "wallet=Buyer111" in seen["url"]
    await c.close()


async def test_claim_paths_singular():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return _ok({"ok": True})

    c = _client(handler)
    await c.claim_build("m", "w")
    await c.claim_creator_fees("m", "w")
    assert "/claim/build/" in seen[0]
    assert "/claim/creator-fees/build/" in seen[1]
    await c.close()


async def test_report_trade_path_and_fields():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content.decode())
        seen["path"] = request.url.path
        return _ok({"status": "processed"})

    c = _client(handler)
    await c.report_trade(signature="sig1", wallet="w", market_id="m",
                         quote_id="qt_1")
    assert seen["path"].endswith("/trades/")
    assert seen["body"]["signature"] == "sig1"
    assert seen["body"]["quoteId"] == "qt_1"
    assert "trade_type" not in seen["body"]
    await c.close()


async def test_create_flow_paths():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return _ok({"ok": True})

    c = _client(handler)
    await c.create_market_quote(
        wallet="w", question="q?", resolution_rule="r",
        sources_of_truth=["https://x.com"], category="crypto",
        start_time=1893456000, end_time=1896127200,
        resolution_time=1896130800, image_url="https://cdn.example.com/i.webp")
    await c.create_market_build(create_id="cr_1")
    await c.create_market_register(create_id="cr_1", signature="sig1")
    assert [p.split("/api/v1")[1] for p in seen] == [
        "/markets/create/quote/", "/markets/create/build/",
        "/markets/register/"]
    await c.close()


async def test_error_envelope_carries_code():
    def handler(request):
        return httpx.Response(404, json={"code": "MARKET_NOT_FOUND",
                                         "message": "no such market"})

    c = _client(handler)
    with pytest.raises(PantaError) as exc:
        await c.market_detail("nope")
    assert exc.value.code == "MARKET_NOT_FOUND"
    assert exc.value.status == 404
    await c.close()


async def test_unauthorized_surfaces():
    def handler(request):
        return httpx.Response(401, json={"code": "UNAUTHORIZED"})

    c = _client(handler, api_key="bad")
    with pytest.raises(PantaError) as exc:
        await c.catalog()
    assert exc.value.code == "UNAUTHORIZED"
    await c.close()


class _FakePanta:
    def __init__(self):
        self.calls = []

    async def primary_buy_quote(self, **kw):
        self.calls.append(kw)
        return {"quoteId": "qt_1", "shares": "38.42"}


def _agent_with_fake_panta():
    from src.agent import AutonomousTradingAgent
    from src.execution import RiskGate
    from src.multi_leg import MultiLegExecutionManager

    class _StubCli:
        async def run(self, *args, **kwargs):
            return {"data": []}

    return AutonomousTradingAgent(
        okx_cli=_StubCli(),
        risk_gate=RiskGate(),
        dry_run=True,
        multi_leg_manager=MultiLegExecutionManager(),
        panta_client=_FakePanta(),
    )


def _cycle_out():
    return {"decisions": [], "signals": [], "errors": []}


async def test_agent_buy_quote_records_decision():
    agent = _agent_with_fake_panta()
    out = _cycle_out()
    res = await agent._panta_phase("PANTA_BUY_EventPda111_YES_20.00_Buyer111",
                                   {}, "c1", out)
    assert res is out
    assert out["decisions"][0]["status"] == "panta_buy_quoted"
    assert agent.panta_client.calls[0] == {
        "market_id": "EventPda111", "wallet": "Buyer111",
        "side": "YES", "amount_usdc": "20.00",
    }


async def test_agent_buy_without_wallet_rejected():
    agent = _agent_with_fake_panta()
    out = _cycle_out()
    await agent._panta_phase("PANTA_BUY_EventPda111_YES_20.00", {}, "c1", out)
    assert out["decisions"] == []
    assert any("wallet" in e for e in out["errors"])
    assert agent.panta_client.calls == []  # no quote attempted


async def test_agent_create_path_retired():
    agent = _agent_with_fake_panta()
    out = _cycle_out()
    await agent._panta_phase("PANTA_CREATE_BTC_UP_BTC_DOWN_50000000",
                             {}, "c1", out)
    assert out["decisions"] == []
    assert any("retired" in e for e in out["errors"])


async def test_agent_normal_asset_untouched():
    agent = _agent_with_fake_panta()
    out = _cycle_out()
    assert await agent._panta_phase("BTC-USDT-SWAP", {}, "c1", out) is None
    assert out == {"decisions": [], "signals": [], "errors": []}
