"""
Panta API client for prediction market integration.

Panta is a binary YES/NO prediction market infrastructure on Solana.
This client covers the documented v1 surface (verified against
https://docs.panta.market 2026-10-09 — every path below matches a
published RequestExample; the previous revision guessed paths like
/markets/catalog and /orders/primary-buy-quote that do not exist).

Custody model: Panta returns unsigned transactions (or instruction lists).
The user's wallet signs. The app broadcasts on its own RPC, then tells
Panta the signature. Never custody wallets.

API Base: https://live-api.panta.market/api/v1
Auth: X-Api-Key header (pk_test_/pk_live_) or Authorization: Bearer.

Key flows:
- Buy: POST /primaryorderquote/ -> POST /primaryorderbuild/ ->
  sign+broadcast -> POST /primaryordersubmit/ -> POST /trades/
- Create: POST /markets/create/quote/ -> POST /markets/create/build/ ->
  sign+broadcast -> POST /markets/register/
- Amounts: creation fees in USDC base units (6 decimals, "50000000");
  primary buys in human-readable decimal strings ("20.00").
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .settings import get_settings

logger = logging.getLogger(__name__)


class PantaError(RuntimeError):
    """Typed Panta failure carrying the API error code (UNAUTHORIZED,
    MARKET_NOT_FOUND, QUOTE_EXPIRED, TX_MISMATCH, ...). Callers switch on
    .code for fail-closed handling instead of parsing message strings."""

    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(f"panta {code}: {message}")
        self.code = code
        self.status = status


class PantaClient:
    """Client for the Panta API v1 (documented paths only)."""

    def __init__(self, api_key: Optional[str] = None,
                 base_url: str = "https://live-api.panta.market/api/v1",
                 http_client=None):
        self.base_url = base_url.rstrip("/")
        self.api_key: Optional[str] = None
        # Resolve api_key: explicit arg > env var from settings
        if api_key:
            self.api_key = api_key
        else:
            settings = get_settings()
            value = getattr(settings, "panta_api_key", None)
            self.api_key = value if isinstance(value, str) else None
        settings = get_settings()
        timeout = getattr(settings, "panta_timeout_seconds", 30) or 30
        # http_client seam: tests inject httpx.MockTransport-backed clients.
        self._client = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=self._default_headers(),
        )

    def _default_headers(self) -> Dict[str, str]:
        if not self.api_key:
            return {}
        # Prefer X-Api-Key; fall back to Authorization Bearer if configured.
        return {"X-Api-Key": self.api_key}

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        await self.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _decode_error(self, resp: httpx.Response) -> PantaError:
        try:
            body = resp.json()
        except Exception:
            body = None
        code, message = "HTTP_ERROR", f"status {resp.status_code}"
        if isinstance(body, dict):
            code = str(body.get("code") or body.get("error") or code)
            message = str(body.get("message") or body.get("detail") or message)
            fields = body.get("fields")
            if fields:
                message = f"{message} (fields: {fields})"
        return PantaError(code, message, resp.status_code)

    async def _get(self, path: str, **params: Any) -> Any:
        resp = await self._client.get(path, params=params)
        if resp.status_code >= 400:
            raise self._decode_error(resp)
        return resp.json()

    async def _post(self, path: str, json: Dict[str, Any], **params: Any) -> Any:
        resp = await self._client.post(path, json=json, params=params)
        if resp.status_code >= 400:
            raise self._decode_error(resp)
        return resp.json()

    # ------------------------------------------------------------------
    # Browse / discover markets
    # ------------------------------------------------------------------

    async def catalog(self, limit: int = 20, cursor: str | None = None,
                      category: str | None = None,
                      status: str | None = None) -> Dict[str, Any]:
        """Paginated USDC market catalog (GET /markets/). List rows carry
        null prices; use market_detail for spot yesPrice/noPrice."""
        params: Dict[str, Any] = {"limit": min(limit, 50)}
        if cursor:
            params["cursor"] = cursor
        if category:
            params["category"] = category
        if status:
            params["status"] = status
        return await self._get("/markets/", **params)

    async def market_detail(self, market_id: str) -> Dict[str, Any]:
        """Single market row with spot prices (GET /markets/{marketId}/)."""
        return await self._get(f"/markets/{market_id}/")

    # ------------------------------------------------------------------
    # Create a market (quote -> build -> broadcast -> register)
    # ------------------------------------------------------------------

    async def create_market_quote(self, *, wallet: str, question: str,
                                  resolution_rule: str,
                                  sources_of_truth: list,
                                  category: str, start_time: int,
                                  end_time: int, resolution_time: int,
                                  image_url: str, title: str | None = None,
                                  description: str | None = None,
                                  market_type: str = "standard") -> Dict[str, Any]:
        """Quote the USDC creation fee (POST /markets/create/quote/).
        Fee is base units (6 decimals); imageUrl is catalog-only."""
        body: Dict[str, Any] = {
            "wallet": wallet,
            "question": question,
            "resolutionRule": resolution_rule,
            "sourcesOfTruth": sources_of_truth,
            "category": category,
            "startTime": start_time,
            "endTime": end_time,
            "resolutionTime": resolution_time,
            "imageUrl": image_url,
            "marketType": market_type,
        }
        if title is not None:
            body["title"] = title
        if description is not None:
            body["description"] = description
        return await self._post("/markets/create/quote/", body)

    async def create_market_build(self, *, create_id: str,
                                  wallet: str | None = None) -> Dict[str, Any]:
        """Unsigned versioned create transaction (POST /markets/create/build/)."""
        body: Dict[str, Any] = {"createId": create_id}
        if wallet is not None:
            body["wallet"] = wallet
        return await self._post("/markets/create/build/", body)

    async def create_market_register(self, *, create_id: str,
                                     signature: str) -> Dict[str, Any]:
        """Register the broadcast create (POST /markets/register/).
        Idempotent on (createId, signature)."""
        return await self._post("/markets/register/",
                                {"createId": create_id, "signature": signature})

    # ------------------------------------------------------------------
    # Primary buy (quote -> build -> broadcast -> submit -> report)
    # ------------------------------------------------------------------

    async def primary_buy_quote(self, *, market_id: str, wallet: str,
                                side: str, amount_usdc: str,
                                user_id: str | None = None) -> Dict[str, Any]:
        """Simulate a YES/NO fill, open a quote session
        (POST /primaryorderquote/). amount_usdc is human-readable ("20.00").
        NOTE: wallet is required (was missing in the first revision)."""
        body: Dict[str, Any] = {
            "wallet": wallet,
            "marketId": market_id,
            "side": side.lower(),
            "amountUsdc": amount_usdc,
        }
        if user_id is not None:
            body["userId"] = user_id
        return await self._post("/primaryorderquote/", body)

    async def primary_buy_build(self, *, quote_id: str, wallet: str,
                                user_id: str | None = None,
                                max_slippage_bps: int = 100) -> Dict[str, Any]:
        """Unsigned primary_order_usdc instructions
        (POST /primaryorderbuild/). Rejects past maxSlippageBps drift."""
        body: Dict[str, Any] = {
            "quoteId": quote_id,
            "wallet": wallet,
            "maxSlippageBps": max_slippage_bps,
        }
        if user_id is not None:
            body["userId"] = user_id
        return await self._post("/primaryorderbuild/", body)

    async def primary_buy_submit(self, *, order_id: str, signature: str,
                                 wallet: str | None = None) -> Dict[str, Any]:
        """Register the broadcast signature (POST /primaryordersubmit/).
        Safe to retry on (orderId, signature)."""
        body: Dict[str, Any] = {"orderId": order_id, "signature": signature}
        if wallet is not None:
            body["wallet"] = wallet
        return await self._post("/primaryordersubmit/", body)

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    async def wallet_positions(self, wallet: str) -> Dict[str, Any]:
        """Holdings for a wallet (GET /positions/?wallet=). Each YES+NO
        holding is two rows (one per side) — expected, not a duplicate."""
        return await self._get("/positions/", wallet=wallet)

    # ------------------------------------------------------------------
    # Claims
    # ------------------------------------------------------------------

    async def claim_build(self, market_id: str, wallet: str) -> Dict[str, Any]:
        """Unsigned claim_win_usdc instructions (POST /claim/build/).
        Check claimable via wallet_positions first."""
        return await self._post("/claim/build/",
                                {"wallet": wallet, "marketId": market_id})

    async def claim_creator_fees(self, market_id: str,
                                 wallet: str) -> Dict[str, Any]:
        """Unsigned claim_creator_fees_usdc instructions
        (POST /claim/creator-fees/build/). Creator + graduated only."""
        return await self._post("/claim/creator-fees/build/",
                                {"wallet": wallet, "marketId": market_id})

    # ------------------------------------------------------------------
    # Trade attribution
    # ------------------------------------------------------------------

    async def report_trade(self, *, signature: str, wallet: str,
                           market_id: str, quote_id: str | None = None,
                           client_order_id: str | None = None,
                           user_id: str | None = None) -> Dict[str, Any]:
        """Attribution for primary buys + win claims (POST /trades/).
        Idempotent per signature. Creator-fee claims are NOT attributable
        (TX_MISMATCH by design)."""
        body: Dict[str, Any] = {
            "signature": signature,
            "wallet": wallet,
            "marketId": market_id,
        }
        if quote_id is not None:
            body["quoteId"] = quote_id
        if client_order_id is not None:
            body["clientOrderId"] = client_order_id
        if user_id is not None:
            body["userId"] = user_id
        return await self._post("/trades/", body)


# Default singleton — initialized from settings when the module is imported.
# main.py can replace / wrap this instance if it wants custom credentials.
_panta_client: Optional[PantaClient] = None


def init_panta_client() -> PantaClient:
    """Create and return a PantaClient instance from settings."""
    global _panta_client
    settings = get_settings()
    key = getattr(settings, "panta_api_key", None) or None
    _panta_client = PantaClient(api_key=key)
    return _panta_client


def get_panta_client() -> Optional[PantaClient]:
    """Return the default PantaClient, or None if not initialised."""
    return _panta_client
