"""Unit tests for the Moove Receive-Agent client (src/moove.py).

Fully offline: every HTTP interaction runs through httpx.MockTransport.
Run: pytest tests/test_moove.py -v
"""
from decimal import Decimal

import httpx
import pytest

from src.moove import (
    MooveClient,
    MooveError,
    MooveNotConfigured,
    client_from_env,
    normalize_amount,
)


def _transport(handler):
    return httpx.MockTransport(handler)


def _ok_create(request: httpx.Request) -> httpx.Response:
    import json

    body = json.loads(request.content.decode())
    assert request.headers["X-API-Key"] == "mk_test_key"
    return httpx.Response(200, json={"id": "link-1", "url": "https://www.moove.xyz/@x/pay/link-1"})


# --- normalize_amount ---


def test_amount_string_passes_through():
    assert normalize_amount("49.99") == "49.99"


def test_amount_int_and_float_render_plain():
    assert normalize_amount(50) == "50"
    assert normalize_amount(49.99) == "49.99"


def test_amount_decimal_supported():
    assert normalize_amount(Decimal("0.50")) == "0.50"


@pytest.mark.parametrize("bad", ["0", "0.00", "-1", "abc", "", True, None, [1]])
def test_amount_rejects_non_positive_and_garbage(bad):
    with pytest.raises(MooveError):
        normalize_amount(bad)


# --- create ---


@pytest.mark.asyncio
async def test_create_returns_id_and_url_only():
    client = MooveClient("mk_test_key", transport=_transport(_ok_create))
    try:
        link = await client.create_payment_link("49.99", description="TARS hire 1", max_usage=1)
    finally:
        await client.aclose()
    assert link == {"id": "link-1", "url": "https://www.moove.xyz/@x/pay/link-1"}


@pytest.mark.asyncio
async def test_create_sends_amount_as_string():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content.decode()))
        return httpx.Response(200, json={"id": "a", "url": "https://x/y"})

    client = MooveClient("k", transport=_transport(handler))
    try:
        await client.create_payment_link(49.99)
    finally:
        await client.aclose()
    assert seen["toAmount"] == "49.99"
    assert isinstance(seen["toAmount"], str)


@pytest.mark.asyncio
async def test_create_truncates_long_description():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content.decode()))
        return httpx.Response(200, json={"id": "a", "url": "https://x/y"})

    client = MooveClient("k", transport=_transport(handler))
    try:
        await client.create_payment_link("1", description="x" * 600)
    finally:
        await client.aclose()
    assert len(seen["description"]) == 500


@pytest.mark.asyncio
async def test_create_rejects_bad_max_usage_without_http():
    client = MooveClient("k", transport=_transport(_ok_create))
    try:
        with pytest.raises(MooveError):
            await client.create_payment_link("1", max_usage=0)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_create_missing_id_url_is_bad_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    client = MooveClient("k", transport=_transport(handler))
    try:
        with pytest.raises(MooveError) as exc:
            await client.create_payment_link("1")
    finally:
        await client.aclose()
    assert exc.value.code == "BAD_RESPONSE"


# --- error taxonomy: branch on code, retry only 429/5xx ---


def _error_case(status, code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"errors": [{"message": "m", "code": code}]})

    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "INVALID_API_KEY", False),
        (403, "INSUFFICIENT_API_SCOPE", False),
        (409, "PAYMENT_LINK_ACCOUNT_NOT_READY", False),
        (422, "INVALID_PAYMENT_LINK_AMOUNT", False),
        (429, "429", True),
        (500, "CANNOT_CREATE_PAYMENT_LINK", True),
    ],
)
async def test_error_taxonomy(status, code, retryable):
    client = MooveClient("k", transport=_transport(_error_case(status, code)))
    try:
        with pytest.raises(MooveError) as exc:
            await client.create_payment_link("1")
    finally:
        await client.aclose()
    assert exc.value.code == code
    assert exc.value.status == status
    assert exc.value.retryable is retryable


@pytest.mark.asyncio
async def test_network_error_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = MooveClient("k", transport=_transport(handler))
    try:
        with pytest.raises(httpx.ConnectError):
            await client.create_payment_link("1")
    finally:
        await client.aclose()


# --- list / retrieve / find ---


@pytest.mark.asyncio
async def test_list_passes_status_and_offset():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(
            200, json={"data": [], "limit": 10, "offset": 5, "nextOffset": None}
        )

    client = MooveClient("k", transport=_transport(handler))
    try:
        page = await client.list_payment_links(status="active", offset=5)
    finally:
        await client.aclose()
    assert seen == {"status": "active", "offset": "5"}
    assert page["nextOffset"] is None


@pytest.mark.asyncio
async def test_retrieve_needs_no_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "X-API-Key" not in request.headers
        assert request.url.path == "/v1/payment-link/link-9"
        return httpx.Response(200, json={"id": "link-9", "status": "active"})

    client = MooveClient("", transport=_transport(handler))
    assert not client.configured
    try:
        link = await client.retrieve_payment_link("link-9")
    finally:
        await client.aclose()
    assert link["id"] == "link-9"


@pytest.mark.asyncio
async def test_find_link_pages_until_found():
    pages = {
        0: {"data": [{"id": "a"}], "limit": 10, "offset": 0, "nextOffset": 10},
        10: {"data": [{"id": "b"}], "limit": 10, "offset": 10, "nextOffset": None},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[int(request.url.params["offset"])])

    client = MooveClient("k", transport=_transport(handler))
    try:
        found = await client.find_link("b")
        missing = await client.find_link("zzz")
    finally:
        await client.aclose()
    assert found == {"id": "b"}
    assert missing is None


# --- configuration honesty ---


@pytest.mark.asyncio
async def test_unconfigured_client_fails_closed_on_scoped_methods():
    client = MooveClient("", transport=_transport(_ok_create))
    try:
        with pytest.raises(MooveNotConfigured):
            await client.create_payment_link("1")
        with pytest.raises(MooveNotConfigured):
            await client.list_payment_links()
        with pytest.raises(MooveNotConfigured):
            await client.find_link("x")
    finally:
        await client.aclose()


def test_client_from_env_reads_key(monkeypatch):
    monkeypatch.setenv("MOOVE_API_KEY", "mk_from_env")
    assert client_from_env().configured


def test_client_from_env_empty_when_unset(monkeypatch):
    monkeypatch.delenv("MOOVE_API_KEY", raising=False)
    assert not client_from_env().configured
