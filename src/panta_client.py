"""
Panta API client for prediction market integration.

Panta is a binary YES/NO prediction market infrastructure on Solana.
This API lets apps browse markets, create markets, buy primary shares,
read positions, build win and creator-fee claim transactions, and report
trades.

Custody model: Panta returns unsigned transactions (or instruction lists).
The user's wallet signs. The app broadcasts on its own RPC, then tells
Panta the signature. Never custody wallets.

API Base: https://live-api.panta.market/api/v1
Auth: X-Api-Key header or Authorization: Bearer <access>

Key flows:
- Quote → build unsigned tx → user signs → app broadcasts → confirm
- Amounts: create market in USDC base units (6 decimals, e.g. "50000000" = 50 USDC)
- Primary buy: human-readable decimal strings, e.g. "20.00"
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .settings import get_settings

logger = logging.getLogger(__name__)


class PantaClient:
    """Client for the Panta API v1."""

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://live-api.panta.market/api/v1"):
        self.base_url = base_url.rstrip("/")
        # Resolve api_key: explicit arg > env var from settings
        if api_key:
            self.api_key = api_key
        else:
            settings = get_settings()
            self.api_key = getattr(settings, "panta_api_key", None)
        settings = get_settings()
        timeout = getattr(settings, "panta_timeout_seconds", 30) or 30
        self._client = httpx.AsyncClient(
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

    async def _get(self, path: str, **params: Any) -> Any:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, json: Dict[str, Any], **params: Any) -> Any:
        resp = await self._client.post(path, json=json, params=params)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Browse / discover markets
    # ------------------------------------------------------------------

    async def catalog(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """List prediction markets in the Panta catalog."""
        return await self._get("/markets/catalog", limit=limit, offset=offset)

    async def market_detail(self, market_id: str) -> Dict[str, Any]:
        """Fetch detail prices and data for a specific market."""
        return await self._get(f"/markets/{market_id}")

    # ------------------------------------------------------------------
    # Create a market
    # ------------------------------------------------------------------

    async def create_market_quote(self, *, outcome_a: str, outcome_b: str, usdc_amount: int) -> Dict[str, Any]:
        """
        Quote the USDC creation fee for a new binary YES/NO market.

        Args:
            outcome_a: The "YES" outcome label (e.g. "BTC price > $50k at T")
            outcome_b: The "NO" outcome label (e.g. "BTC price <= $50k at T")
            usdc_amount: Creation fee in USDC base units (6 decimals), e.g. 50000000 = 50 USDC

        Returns:
            Quote dict from Panta API with unsigned transaction instructions.
        """
        return await self._post(
            "/markets/create-quote",
            {
                "outcome_a": outcome_a,
                "outcome_b": outcome_b,
                "usdc_amount": usdc_amount,
            },
        )

    # ------------------------------------------------------------------
    # Primary buy (YES / NO position)
    # ------------------------------------------------------------------

    async def primary_buy_quote(self, *, market_id: str, outcome: str, usdc_amount: str) -> Dict[str, Any]:
        """
        Quote a YES/NO primary fill on the bonding curve.

        Args:
            market_id: Panta market identifier
            outcome: "YES" or "NO"
            usdc_amount: Human-readable decimal string, e.g. "20.00"

        Returns:
            Quote dict with unsigned transaction instructions.
        """
        return await self._post(
            "/orders/primary-buy-quote",
            {
                "market_id": market_id,
                "outcome": outcome,
                "usdc_amount": usdc_amount,
            },
        )

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    async def wallet_positions(self, wallet: str) -> Dict[str, Any]:
        """List a wallet's USDC holdings, phase, and claim eligibility."""
        return await self._get(f"/positions/{wallet}")

    # ------------------------------------------------------------------
    # Claims
    # ------------------------------------------------------------------

    async def claim_build(self, market_id: str, wallet: str) -> Dict[str, Any]:
        """Build unsigned claim instructions when a resolved market is claimable."""
        return await self._post(
            "/claims/build",
            {
                "market_id": market_id,
                "wallet": wallet,
            },
        )

    async def claim_creator_fees(self, market_id: str, wallet: str) -> Dict[str, Any]:
        """Build unsigned instructions to withdraw graduated-market creator fees."""
        return await self._post(
            "/claims/creator-fees",
            {
                "market_id": market_id,
                "wallet": wallet,
            },
        )

    # ------------------------------------------------------------------
    # Trade attribution
    # ------------------------------------------------------------------

    async def report_trade(self, market_id: str, signature: str, trade_type: str) -> Dict[str, Any]:
        """Verify on-chain buys and win claims for volume reporting."""
        return await self._post(
            "/trades/report",
            {
                "market_id": market_id,
                "signature": signature,
                "trade_type": trade_type,
            },
        )


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