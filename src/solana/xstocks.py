"""xStocks API adapter — real response shapes (probed 2026-09-17).

Canonical symbols use the exchange suffix-x convention: AAPLx, TSLAx, NVDAx
(NOT the xAAPL prefix form). All public endpoints, no auth needed for demo.

Shapes (api.xstocks.fi/api/v2):
  GET /public/assets                       -> {"nodes": [{symbol, name, deployments[], ...}]}
  GET /public/assets/{sym}                 -> single asset node (deployments[] per network)
  GET /public/assets/{sym}/price-data      -> {"quote": float}
  GET /public/assets/{sym}/multiplier?network=Solana
                                           -> {"currentMultiplier": f, "newMultiplier": f, ...}
  GET /public/assets/{sym}/multiplier/history?network=Solana
                                           -> {"page": {...}, "nodes": [{reason, multiplier, ...}]}
  GET /public/corporate-actions/upcoming   -> {"page": {...}, "nodes": [{xstockSymbol, caType, effectiveTimeUtc}]}
  GET /public/oracles/{sym}                -> {"nodes": [{feedId (Pyth hex), decimals, ...}]}
  GET /public/proof-of-reserves/{sym}      -> {"symbol", "sharesHeld", "circulatingSupply", ...}

Solana mint: asset["deployments"] entry with network == "Solana" -> address.
Display price = raw quote * currentMultiplier (Token-2022 Scaled UI extension).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx
from typing import Any

BASE = "https://api.xstocks.fi/api/v2"
TIMEOUT = 20.0
NETWORK = "Solana"

# Canonical demo universe (suffix-x exchange symbols).
DEMO_SYMBOLS = ["AAPLx", "TSLAx", "NVDAx"]


class XStocksClient:
    def __init__(self, base: str = BASE, timeout: float = TIMEOUT):
        self.base = base
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)
        self._cache: dict[str, Any] = {}

    async def close(self):
        await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base}{path}"
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    # --- Public endpoints ---

    async def list_assets(self) -> list[dict]:
        """GET /public/assets — unwrap {"nodes": [...]}."""
        if "assets" not in self._cache:
            data = await self._get("/public/assets")
            nodes = data.get("nodes", data) if isinstance(data, dict) else data
            self._cache["assets"] = nodes if isinstance(nodes, list) else []
        return self._cache["assets"]

    async def get_asset(self, symbol: str) -> dict:
        """GET /public/assets/{symbol} — full node (deployments, trading flags)."""
        return await self._get(f"/public/assets/{symbol}")

    async def get_price(self, symbol: str) -> dict:
        """GET /public/assets/{symbol}/price-data — normalize {"quote"} to {"price"}."""
        data = await self._get(f"/public/assets/{symbol}/price-data")
        price = float(data.get("quote", data.get("price", 0)))
        return {"price": price, "source": data.get("source", "xstocks")}

    async def get_multiplier(self, symbol: str, network: str = NETWORK) -> dict:
        """Multiplier — normalize to {"current", "pending"}."""
        data = await self._get(
            f"/public/assets/{symbol}/multiplier", params={"network": network}
        )
        current = float(data.get("currentMultiplier", data.get("current", 1.0)))
        pending = float(data.get("newMultiplier", data.get("pending", 0.0)))
        return {"current": current, "pending": pending}

    async def get_multiplier_history(
        self, symbol: str, network: str = NETWORK
    ) -> list[dict]:
        """Dividend/split history — unwrap {"nodes": [...]}."""
        data = await self._get(
            f"/public/assets/{symbol}/multiplier/history", params={"network": network}
        )
        if isinstance(data, dict):
            return data.get("nodes", [])
        return data if isinstance(data, list) else []

    async def get_upcoming_corporate_actions(self) -> list[dict]:
        """Upcoming earnings/div calendar — unwrap {"nodes": [...]} (page 1)."""
        data = await self._get("/public/corporate-actions/upcoming")
        if isinstance(data, dict):
            return data.get("nodes", [])
        return data if isinstance(data, list) else []

    async def get_oracles(self, symbol: str) -> list[dict]:
        """Oracle PDAs/feeds — unwrap {"nodes": [...]}, includes Pyth feedId."""
        data = await self._get(f"/public/oracles/{symbol}")
        if isinstance(data, dict):
            return data.get("nodes", [])
        return data if isinstance(data, list) else []

    async def get_proof_of_reserves(self, symbol: str) -> dict:
        """Backing check — {"sharesHeld", "circulatingSupply", ...}."""
        return await self._get(f"/public/proof-of-reserves/{symbol}")

    # --- Helpers for our agent ---

    async def get_spot_price(self, symbol: str) -> float:
        """Display spot = raw quote * currentMultiplier."""
        data = await self.get_price(symbol)
        price = float(data.get("price", 0))
        mult_data = await self.get_multiplier(symbol)
        mult = float(mult_data.get("current", 1.0))
        return price * mult

    async def get_solana_mint(self, symbol: str) -> str | None:
        """Solana mint from deployments[] (network == "Solana")."""
        asset = await self.get_asset(symbol)
        for dep in asset.get("deployments", []):
            if dep.get("network") == "Solana":
                return dep.get("address")
        return None

    async def get_dividend_yield_proxy(self, symbol: str) -> float:
        """Rough annualized div yield from multiplier history dividend events.

        Each dividend bumps the multiplier; sum the fractional bumps over the
        trailing year as a yield proxy. Returns 0.0 when no history.
        """
        try:
            nodes = await self.get_multiplier_history(symbol)
        except Exception:
            return 0.0
        cutoff = time.time() - 365 * 86400
        total = 0.0
        for ev in nodes:
            if (ev.get("reason") or "").lower() != "dividend":
                continue
            try:
                ts = datetime.fromisoformat(
                    ev["activationDateTime"].replace("Z", "+00:00")
                ).timestamp()
            except (KeyError, ValueError):
                continue
            if ts < cutoff:
                continue
            prev = float(ev.get("previousMultiplier", 0) or 0)
            cur = float(ev.get("multiplier", 0) or 0)
            if prev > 0 and cur > 0:
                total += max(0.0, cur / prev - 1.0)
        return total

    async def get_corporate_action_blackout(self, hours_ahead: int = 24) -> dict[str, float]:
        """{symbol: action_timestamp} for actions within hours_ahead."""
        try:
            actions = await self.get_upcoming_corporate_actions()
        except Exception:
            return {}
        blackout = {}
        now = time.time()
        cutoff = now + hours_ahead * 3600
        for action in actions:
            ts_raw = action.get("effectiveTimeUtc") or action.get("timestamp", 0)
            try:
                ts = (
                    datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).timestamp()
                    if isinstance(ts_raw, str)
                    else float(ts_raw)
                )
            except (ValueError, TypeError):
                continue
            if now < ts < cutoff:
                blackout[action.get("xstockSymbol", action.get("symbol", ""))] = ts
        return blackout

    async def get_backing_ratio(self, symbol: str) -> float | None:
        """sharesHeld / circulatingSupply; None when unavailable."""
        try:
            por = await self.get_proof_of_reserves(symbol)
            held = float(por.get("sharesHeld", 0))
            supply = float(por.get("circulatingSupply", 0))
            return held / supply if supply > 0 else None
        except Exception:
            return None


async def demo():
    client = XStocksClient()
    try:
        assets = await client.list_assets()
        print(f"Found {len(assets)} assets")
        for sym in DEMO_SYMBOLS:
            price = await client.get_spot_price(sym)
            mint = await client.get_solana_mint(sym)
            print(f"{sym} spot={price:.4f} mint={mint}")
    finally:
        await client.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(demo())
