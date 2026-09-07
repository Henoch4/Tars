"""Moove agentic payments client (Receive Agent).

Live surface only: the Moove Receive Agent with ``payment_link:create`` +
``payment_link:read`` scopes. Send/Swap/Bridge/Ramp APIs are announced but
not live — nothing here depends on them.

Docs: https://docs.moove.xyz/transact/moove-agentic-payments
Spec:  https://api.moove.xyz/openapi.json

Security posture (mirrors the platform's own guarantees):
- The key is a down-scope of its owner: links always settle to the key
  owner's own default wallet and the caller cannot specify a destination.
- The key value is never logged, never returned by any function, and only
  read from the ``MOOVE_API_KEY`` environment variable.
- Branch on error ``code``, never on ``message``. Only 429/5xx + network
  errors are retryable; 401/403/409/422 mean the caller (or the account
  setup) must act — do not retry.
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

MOOVE_API_BASE = "https://api.moove.xyz"
MOOVE_KEY_ENV = "MOOVE_API_KEY"

LinkStatus = Literal["active", "inactive", "completed"]

# Statuses worth retrying with backoff. Everything else is caller/account
# action (new key, right scope, claim handle + default wallet, fix amount).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class MooveError(Exception):
    """A failed Moove API call, with machine-branchable fields."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        status: int = 0,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retryable = retryable


class MooveNotConfigured(MooveError):
    """Raised when no API key is available — the honest 503 path."""

    def __init__(self) -> None:
        super().__init__(
            "Moove is not configured. Set MOOVE_API_KEY (Moove Receive Agent) "
            "before calling billing endpoints.",
            code="MOOVE_NOT_CONFIGURED",
            status=503,
            retryable=False,
        )


def normalize_amount(value: str | int | float | Decimal) -> str:
    """Render an amount as a plain decimal string for ``toAmount``.

    The API quantises against the settlement token's decimals and rejects
    with 422 INVALID_PAYMENT_LINK_AMOUNT when precision is exceeded — so
    floats must never be forwarded raw (``str(49.99)`` is fine, arithmetic
    on floats is not). Strings pass through untouched (trailing zeros are
    not significant per the docs); numbers go through ``Decimal(str(v))``
    to avoid binary-float artifacts.
    """
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, Decimal):
        text = format(value, "f")
    elif isinstance(value, bool):
        raise MooveError(
            f"Invalid amount: {value!r}",
            code="INVALID_AMOUNT",
            retryable=False,
        )
    elif isinstance(value, (int, float)):
        text = format(Decimal(str(value)), "f")
    else:
        raise MooveError(
            f"Invalid amount type: {type(value).__name__}",
            code="INVALID_AMOUNT",
            retryable=False,
        )
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        raise MooveError(
            f"Invalid amount: {value!r}",
            code="INVALID_AMOUNT",
            retryable=False,
        ) from None
    if amount <= 0:
        raise MooveError(
            f"Amount must be greater than 0: {value!r}",
            code="INVALID_AMOUNT",
            retryable=False,
        )
    return format(amount, "f")


def _raise_for_response(resp: httpx.Response) -> Any:
    """Return decoded JSON on 2xx, else raise a branchable MooveError."""
    if 200 <= resp.status_code < 300:
        try:
            return resp.json()
        except ValueError:
            raise MooveError(
                "Moove returned non-JSON success body",
                code="BAD_RESPONSE",
                status=resp.status_code,
                retryable=False,
            ) from None
    code, message = "", ""
    try:
        errors = resp.json().get("errors", [])
        if errors:
            code = str(errors[0].get("code", ""))
            message = str(errors[0].get("message", ""))
    except ValueError:
        message = resp.text[:200]
    if not message:
        message = f"Moove request failed with status {resp.status_code}"
    raise MooveError(
        message,
        code=code,
        status=resp.status_code,
        retryable=resp.status_code in _RETRYABLE_STATUS,
    )


class MooveClient:
    """Async client for the Moove Payments API (Receive Agent)."""

    def __init__(
        self,
        api_key: str = "",
        *,
        base_url: str = MOOVE_API_BASE,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-API-Key": api_key} if api_key else {},
            timeout=timeout,
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _require_key(self) -> None:
        if not self._api_key:
            raise MooveNotConfigured()

    async def create_payment_link(
        self,
        to_amount: str | int | float | Decimal,
        *,
        description: str | None = None,
        max_usage: int | None = None,
        expiration_date: str | None = None,
    ) -> dict:
        """Create a payment link. Returns ``{"id": ..., "url": ...}`` only —
        status/token/amounts require the list endpoint (by API design)."""
        self._require_key()
        body: dict[str, Any] = {"toAmount": normalize_amount(to_amount)}
        if description is not None:
            body["description"] = description[:500]
        if max_usage is not None:
            if max_usage < 1:
                raise MooveError(
                    f"max_usage must be >= 1: {max_usage!r}",
                    code="INVALID_AMOUNT",
                    retryable=False,
                )
            body["maxUsage"] = max_usage
        if expiration_date is not None:
            body["expirationDate"] = expiration_date
        resp = await self._client.post("/v1/payment-link", json=body)
        data = _raise_for_response(resp)
        if not isinstance(data, dict) or "id" not in data or "url" not in data:
            raise MooveError(
                "Moove create response missing id/url",
                code="BAD_RESPONSE",
                status=resp.status_code,
                retryable=False,
            )
        return {"id": data["id"], "url": data["url"]}

    async def list_payment_links(
        self,
        *,
        status: LinkStatus | None = None,
        offset: int = 0,
    ) -> dict:
        """Newest-first page (10 fixed). Follow ``nextOffset`` until null."""
        self._require_key()
        params: dict[str, Any] = {"offset": max(0, offset)}
        if status is not None:
            params["status"] = status
        resp = await self._client.get("/v1/payment-link", params=params)
        return _raise_for_response(resp)

    async def retrieve_payment_link(self, link_id: str) -> dict:
        """Public lookup (unauthenticated by design — the hosted checkout
        page calls it). Enriched with the owner's public profile."""
        if not link_id:
            raise MooveError(
                "link_id is required",
                code="INVALID_AMOUNT",
                retryable=False,
            )
        resp = await self._client.get(f"/v1/payment-link/{link_id}")
        return _raise_for_response(resp)

    async def find_link(self, link_id: str, *, limit_pages: int = 5) -> dict | None:
        """Reconcile helper: page the (scoped) list endpoint for one id.

        A key sees every link its owner can see — dashboard-created and
        sibling-key links included — so list is the reconciliation source.
        """
        self._require_key()
        offset: int | None = 0
        for _ in range(max(1, limit_pages)):
            if offset is None:
                return None
            page = await self.list_payment_links(offset=offset)
            for link in page.get("data", []):
                if link.get("id") == link_id:
                    return link
            offset = page.get("nextOffset")
        return None

    async def aclose(self) -> None:
        await self._client.aclose()


def client_from_env(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> MooveClient:
    """Build a client from ``MOOVE_API_KEY``. Never raises when unset —
    the returned client reports ``configured == False`` and each scoped
    method raises ``MooveNotConfigured`` (the honest 503 path)."""
    return MooveClient(
        os.getenv(MOOVE_KEY_ENV, "").strip(),
        transport=transport,
    )
