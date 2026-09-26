"""Pyth SOL/USD adapter — keyed Hermes (post Aug-2026 upgrade), env fallback.

Docs (read thoroughly 2026-09-19, https://docs.pyth.network/price-feeds/core/upgrade/preparing):
- Hermes now requires an API key for EVERYONE (free trial at pythdata.app/signup).
- New endpoint https://pyth.dourolabs.app/hermes is a drop-in replacement:
  same routes, same response shapes. Old hermes.pyth.network also works WITH key.
- Auth:  Authorization: Bearer $PYTH_API_KEY
- SOL/USD feed id: ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d
  (confirmed in the Solana push-feed table; shard-0 account on devnet:
  7UVimffxr9ow1uXYxsr4LHAcV58mLzhmwaeKvJ1pjLiE).
- Shape: GET {base}/v2/updates/price/latest?ids[]=<id> ->
  {"parsed": [{"price": {"price": str, "conf": str, "expo": int,
  "publish_time": int}}]}; usd = price * 10**expo.

PLACEHOLDER MODE (today): no PYTH_API_KEY set -> the client never calls the
network and reports available=False; callers fall back to SOL_PRICE_USD env,
then 150.0. Fill the key in .env to go live — no code change needed.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

HERMES_URL = "https://pyth.dourolabs.app/hermes"
SOL_USD_ID = "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d"
CACHE_TTL_S = 60.0


class PythClient:
    """Keyed Hermes client with TTL cache. Keyless = placeholder (no calls)."""

    def __init__(
        self,
        api_key: str | None = None,
        base: str = HERMES_URL,
        feed_id: str = SOL_USD_ID,
        timeout: float = 20.0,
    ):
        self.api_key = api_key or os.getenv("PYTH_API_KEY", "") or None
        self.base = base.rstrip("/")
        self.feed_id = feed_id
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._cached: dict[str, Any] = {}

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def get_sol_price_usd(self) -> dict[str, Any]:
        """Live SOL/USD from Hermes, or {"available": False} without a key."""
        now = time.time()
        if self._cached and now - self._cached.get("at", 0) < CACHE_TTL_S:
            return self._cached["quote"]
        if not self.api_key:
            return {"available": False, "source": "pyth-missing-key"}
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        resp = await self._client.get(
            f"{self.base}/v2/updates/price/latest",
            params={"ids[]": self.feed_id},
            headers=self._headers(),
        )
        resp.raise_for_status()
        node = resp.json()["parsed"][0]["price"]
        quote = {
            "available": True,
            "price": float(node["price"]) * (10.0 ** int(node["expo"])),
            "conf": float(node["conf"]) * (10.0 ** int(node["expo"])),
            "expo": int(node["expo"]),
            "publish_time": int(node["publish_time"]),
            "source": "pyth-hermes",
        }
        self._cached = {"at": now, "quote": quote}
        return quote

    async def sol_usd_with_fallback(self, fallback: float) -> tuple[float, str]:
        """(price, source) — Hermes when keyed, else the provided fallback."""
        try:
            quote = await self.get_sol_price_usd()
        except Exception:
            quote = {"available": False, "source": "pyth-error"}
        if quote.get("available") and quote.get("price", 0) > 0:
            return float(quote["price"]), str(quote.get("source", "pyth-hermes"))
        return float(fallback), f"{quote.get('source', 'pyth-unavailable')}+env"
