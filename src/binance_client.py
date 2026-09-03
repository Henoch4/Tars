"""
Binance REST API client — drop-in replacement for OkxCli.

Implements the same async interface (run, check_auth, balance_all, positions,
smartmoney_signal) so the agent, executor, and scheduler work unchanged.
Authenticated endpoints use HMAC-SHA256 signing per Binance API docs.

Instrument mapping: the agent works in OKX-style names (BTC-USDT-SWAP);
this client converts to Binance format (BTCUSDT) transparently.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Binance endpoints
BINANCE_API_BASE = "https://api.binance.com"
BINANCE_TESTNET_BASE = "https://testnet.binance.vision"
# Futures use a separate host from spot.
BINANCE_FUTURES_BASE = "https://fapi.binance.com"
BINANCE_FUTURES_TESTNET_BASE = "https://testnet.binancefuture.com"

# Instrument name mapping: OKX -> Binance
INST_MAP_OKX_TO_BINANCE: dict[str, str] = {
    "BTC-USDT-SWAP": "BTCUSDT",
    "ETH-USDT-SWAP": "ETHUSDT",
    "SOL-USDT-SWAP": "SOLUSDT",
    "BNB-USDT-SWAP": "BNBUSDT",
    "XRP-USDT-SWAP": "XRPUSDT",
    "DOGE-USDT-SWAP": "DOGEUSDT",
    "ADA-USDT-SWAP": "ADAUSDT",
    "AVAX-USDT-SWAP": "AVAXUSDT",
    "DOT-USDT-SWAP": "DOTUSDT",
    "LINK-USDT-SWAP": "LINKUSDT",
    "MATIC-USDT-SWAP": "MATICUSDT",
    "UNI-USDT-SWAP": "UNIUSDT",
    "SHIB-USDT-SWAP": "SHIBUSDT",
    "LTC-USDT-SWAP": "LTCUSDT",
    "BCH-USDT-SWAP": "BCHUSDT",
    "ATOM-USDT-SWAP": "ATOMUSDT",
    "NEAR-USDT-SWAP": "NEARUSDT",
    "APT-USDT-SWAP": "APTUSDT",
    "ARB-USDT-SWAP": "ARBUSDT",
    "OP-USDT-SWAP": "OPUSDT",
    "FIL-USDT-SWAP": "FILUSDT",
    "RENDER-USDT-SWAP": "RENDERUSDT",
    "SUI-USDT-SWAP": "SUIUSDT",
    "SEI-USDT-SWAP": "SEIUSDT",
    "TIA-USDT-SWAP": "TIAUSDT",
}

INST_MAP_SPOT: dict[str, str] = {
    "BTC-USDT": "BTCUSDT",
    "ETH-USDT": "ETHUSDT",
    "SOL-USDT": "SOLUSDT",
    "BNB-USDT": "BNBUSDT",
    "XRP-USDT": "XRPUSDT",
    "DOGE-USDT": "DOGEUSDT",
    "ADA-USDT": "ADAUSDT",
    "AVAX-USDT": "AVAXUSDT",
    "DOT-USDT": "DOTUSDT",
    "LINK-USDT": "LINKUSDT",
    "MATIC-USDT": "MATICUSDT",
    "UNI-USDT": "UNIUSDT",
    "SHIB-USDT": "SHIBUSDT",
    "LTC-USDT": "LTCUSDT",
    "BCH-USDT": "BCHUSDT",
    "ATOM-USDT": "ATOMUSDT",
    "NEAR-USDT": "NEARUSDT",
    "APT-USDT": "APTUSDT",
    "ARB-USDT": "ARBUSDT",
    "OP-USDT": "OPUSDT",
    "FIL-USDT": "FILUSDT",
    "RENDER-USDT": "RENDERUSDT",
    "SUI-USDT": "SUIUSDT",
    "SEI-USDT": "SEIUSDT",
    "TIA-USDT": "TIAUSDT",
}


class BinanceClientError(RuntimeError):
    def __init__(self, endpoint: str, status: int, body: str):
        self.endpoint = endpoint
        self.status = status
        self.body = body
        super().__init__(f"Binance {endpoint} failed (HTTP {status}): {body[:200]}")


@dataclass
class BinanceConfig:
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = False  # default: mainnet for real trading
    base_url: str | None = None


def _map_instrument(inst_id: str) -> str:
    if inst_id in INST_MAP_OKX_TO_BINANCE:
        return INST_MAP_OKX_TO_BINANCE[inst_id]
    if inst_id in INST_MAP_SPOT:
        return INST_MAP_SPOT[inst_id]
    return inst_id.replace("-", "").upper()


def _is_swap(inst_id: str) -> bool:
    return inst_id.endswith("-SWAP")


class BinanceClient:
    """Async Binance REST API client using httpx (already in requirements.txt)."""

    def __init__(self, config: BinanceConfig):
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._instruments_cache: dict[str, dict] = {}
        # Server-time offset (ms): server_time - local_time, refreshed
        # periodically. Corrects for local clock drift so signed requests
        # stay inside Binance's recvWindow (default 5000ms).
        self._time_offset_ms: int = 0
        self._time_offset_fetched_at: float = 0.0

    @property
    def _base_url(self) -> str:
        if self.config.base_url:
            return self.config.base_url
        return BINANCE_TESTNET_BASE if self.config.testnet else BINANCE_API_BASE

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"X-MBX-APIKEY": self.config.api_key},
                timeout=30.0,
            )
        return self._client

    async def _sync_time(self) -> None:
        """Fetch Binance server time and cache the offset vs local clock.

        Refreshed at most every 10 minutes. Best-effort: if the time endpoint
        is unreachable we keep the last known offset (starting at 0) rather
        than blocking all signed requests on a clock read.
        """
        now = time.time()
        if now - self._time_offset_fetched_at < 600 and self._time_offset_fetched_at > 0:
            return
        try:
            client = await self._ensure_client()
            resp = await client.get("/api/v3/time")
            if resp.status_code == 200:
                server_ms = int(resp.json().get("serverTime", 0))
                if server_ms > 0:
                    self._time_offset_ms = server_ms - int(now * 1000)
                    self._time_offset_fetched_at = now
        except Exception as e:
            logger.warning(f"Binance time sync failed, using cached offset: {e}")

    async def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add server-synced timestamp, recvWindow, and HMAC-SHA256 signature."""
        await self._sync_time()
        params["timestamp"] = int(time.time() * 1000) + self._time_offset_ms
        params.setdefault("recvWindow", 10000)
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.config.api_secret.encode(),
            query_string.encode(),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        client = await self._ensure_client()
        params = params or {}
        if signed:
            params = await self._sign(params)

        # Futures endpoints live on a separate host from spot — and the
        # testnet host differs from mainnet. Never send testnet-signed
        # requests to the mainnet futures host (or vice versa).
        if endpoint.startswith("/fapi/"):
            host = BINANCE_FUTURES_TESTNET_BASE if self.config.testnet else BINANCE_FUTURES_BASE
            url = f"{host}{endpoint}"
        else:
            url = None  # use base_url

        try:
            if url:
                resp = await client.request(method, url, params=params)
            else:
                resp = await client.request(method, endpoint, params=params)
            if resp.status_code != 200:
                raise BinanceClientError(endpoint, resp.status_code, resp.text)
            return resp.json() if resp.text else {}
        except httpx.HTTPError as e:
            raise BinanceClientError(endpoint, -1, str(e))

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------

    async def get_price(self, symbol: str) -> float:
        data = await self._request("GET", "/api/v3/ticker/price", {"symbol": symbol})
        return float(data.get("price", 0))

    async def get_klines(self, symbol: str, interval: str = "1h", limit: int = 100) -> list[list]:
        return await self._request("GET", "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})

    async def get_funding_rate(self, symbol: str) -> dict:
        try:
            data = await self._request("GET", "/fapi/v1/fundingRate", {"symbol": symbol, "limit": 1})
            if data and isinstance(data, list):
                return data[0]
        except Exception:
            pass
        return {"symbol": symbol, "fundingRate": "0"}

    async def get_exchange_info(self, symbol: str) -> dict:
        if symbol in self._instruments_cache:
            return self._instruments_cache[symbol]
        data = await self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})
        symbols = data.get("symbols", [])
        info = symbols[0] if symbols else {}
        self._instruments_cache[symbol] = info
        return info

    # ------------------------------------------------------------------
    # Authenticated endpoints
    # ------------------------------------------------------------------

    async def account_balance(self) -> dict:
        data = await self._request("GET", "/api/v3/account", signed=True)
        balances = data.get("balances", [])
        total_usd = 0.0
        details = []
        for b in balances:
            free = float(b.get("free", 0))
            locked = float(b.get("locked", 0))
            if free > 0 or locked > 0:
                asset = b["asset"]
                if asset in ("USDT", "USDC", "BUSD", "FDUSD"):
                    total_usd += free + locked
                details.append({"ccy": asset, "availBal": str(free), "frozenBal": str(locked), "eq": str(free + locked)})
        return {"trading": {"totalEq": str(total_usd), "details": details}, "funding": {"details": []}}

    async def futures_balance(self) -> dict:
        try:
            data = await self._request("GET", "/fapi/v2/balance", signed=True)
            total = sum(float(a.get("balance", 0)) for a in data if a.get("asset") == "USDT")
            return {"totalBalance": str(total), "assets": data}
        except Exception:
            return {"totalBalance": "0", "assets": []}

    async def futures_position(self, symbol: str) -> dict | None:
        try:
            data = await self._request("GET", "/fapi/v2/positionRisk", signed=True)
            for pos in data:
                if pos.get("symbol") == symbol and float(pos.get("positionAmt", 0)) != 0:
                    return pos
        except Exception:
            pass
        return None

    async def place_order(
        self, symbol: str, side: str, quantity: str,
        order_type: str = "MARKET", price: str | None = None,
        time_in_force: str | None = None, reduce_only: bool = False,
        client_order_id: str | None = None,
    ) -> dict:
        is_swap = _is_swap(symbol)
        binance_symbol = _map_instrument(symbol)

        if is_swap:
            endpoint = "/fapi/v1/order"
            params: dict[str, Any] = {"symbol": binance_symbol, "side": side.upper(), "type": order_type.upper(), "quantity": quantity}
            if client_order_id:
                params["newClientOrderId"] = client_order_id
            if order_type.upper() == "LIMIT":
                params["price"] = price
                params["timeInForce"] = time_in_force or "GTC"
            if reduce_only:
                params["reduceOnly"] = "true"
        else:
            endpoint = "/api/v3/order"
            params = {"symbol": binance_symbol, "side": side.upper(), "type": order_type.upper(), "quantity": quantity}
            if client_order_id:
                params["newClientOrderId"] = client_order_id
            if order_type.upper() == "LIMIT":
                params["price"] = price
                params["timeInForce"] = time_in_force or "GTC"

        result = await self._request("POST", endpoint, params, signed=True)

        return {
            "data": [{
                "ordId": str(result.get("orderId", "")),
                "clOrdId": result.get("clientOrderId", client_order_id or ""),
                "state": _map_order_status(result.get("status", "NEW")),
                "accFillSz": result.get("executedQty", "0"),
                "fillPx": result.get("avgPrice") or result.get("price", "0"),
                "fillSz": result.get("executedQty", "0"),
                "fillUsd": str(float(result.get("executedQty", 0) or 0) * float(result.get("avgPrice") or result.get("price", 0) or 0)),
                "fee": "0",
                "fee_ccy": "USDT",
            }],
            "_raw": result,
        }

    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        binance_symbol = _map_instrument(symbol)
        endpoint = "/fapi/v1/order" if _is_swap(symbol) else "/api/v3/order"
        # _request signs when signed=True — no pre-sign here (would double-sign).
        params = {"symbol": binance_symbol, "orderId": order_id}
        return await self._request("DELETE", endpoint, params, signed=True)

    async def get_order_status(self, symbol: str, order_id: str) -> dict:
        binance_symbol = _map_instrument(symbol)
        endpoint = "/fapi/v1/order" if _is_swap(symbol) else "/api/v3/order"
        params = {"symbol": binance_symbol, "orderId": order_id}
        result = await self._request("GET", endpoint, params, signed=True)
        return {
            "data": [{
                "ordId": str(result.get("orderId", "")),
                "clOrdId": result.get("clientOrderId", ""),
                "state": _map_order_status(result.get("status", "NEW")),
                "accFillSz": result.get("executedQty", "0"),
                "fillPx": result.get("avgPrice") or result.get("price", "0"),
                "fillSz": result.get("executedQty", "0"),
                "fillUsd": str(float(result.get("executedQty", 0) or 0) * float(result.get("avgPrice") or result.get("price", 0) or 0)),
                "fee": "0",
                "fee_ccy": "USDT",
            }],
        }

    # ------------------------------------------------------------------
    # OkxCli-compatible interface
    # ------------------------------------------------------------------

    async def check_auth(self) -> dict:
        if not self.config.api_key:
            return {"authenticated": False, "method": None, "detail": "No API key configured"}
        try:
            await self.account_balance()
            return {"authenticated": True, "method": "api_key", "detail": {"exchange": "binance", "testnet": self.config.testnet}}
        except Exception as e:
            return {"authenticated": False, "method": None, "detail": str(e)}

    async def balance_all(self) -> dict:
        spot = await self.account_balance()
        futures = await self.futures_balance()
        total_usd = float(spot.get("trading", {}).get("totalEq", 0)) + float(futures.get("totalBalance", 0))
        return {
            "trading": {"totalEq": str(total_usd), "adjEq": str(total_usd), "details": spot.get("trading", {}).get("details", [])},
            "funding": spot.get("funding", {"details": []}),
            "valuation": {"totalEq": str(total_usd)},
        }

    async def positions(self, inst_type: str | None = None) -> list[dict]:
        common_swaps = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "BNB-USDT-SWAP"]
        positions = []
        for swap in common_swaps:
            pos = await self.futures_position(_map_instrument(swap))
            if pos:
                amt = float(pos.get("positionAmt", 0))
                if amt != 0:
                    positions.append({
                        "instId": swap, "instType": "SWAP",
                        "pos": str(amt), "side": "long" if amt > 0 else "short",
                        "avgPx": pos.get("entryPrice", "0"),
                        "upl": pos.get("unRealizedProfit", "0"),
                        "lever": pos.get("leverage", "1"),
                    })
        return positions

    async def smartmoney_signal(self, inst_ccy: str) -> dict:
        return {"weightedLongRatio": 0.5, "weightedShortRatio": 0.5, "netNotionalUsdt": 0, "note": "Not available on Binance"}

    async def run(self, *args: str, use_global_flags: bool = True) -> Any:
        if not args:
            raise BinanceClientError("run", 400, "No command specified")
        command = args[0]
        if command == "market" and len(args) >= 2:
            sub = args[1]
            symbol = args[2] if len(args) > 2 else ""
            if sub == "trades":
                return await self._market_trades(symbol)
            elif sub == "funding-rate":
                return await self._market_funding_rate(symbol)
            elif sub == "instruments":
                return await self._market_instruments(args)
        elif command == "trade" and len(args) >= 2:
            if args[1] == "order":
                return await self._trade_order(args)
            elif args[1] == "cancel":
                return await self._trade_cancel(args)
        elif command == "account" and len(args) >= 2:
            if args[1] == "balance-all":
                return await self.balance_all()
            elif args[1] == "positions":
                inst_id = None
                for i, a in enumerate(args):
                    if a == "--instId" and i + 1 < len(args):
                        inst_id = args[i + 1]
                if inst_id:
                    pos = await self.futures_position(_map_instrument(inst_id))
                    return [pos] if pos else []
                return await self.positions()
        raise BinanceClientError("run", 400, f"Unknown command: {' '.join(args)}")

    async def _market_trades(self, symbol: str) -> list[dict]:
        binance_symbol = _map_instrument(symbol)
        data = await self._request("GET", "/api/v3/trades", {"symbol": binance_symbol, "limit": 50})
        return [{"px": t.get("price", "0"), "sz": t.get("qty", "0"), "ts": int(t.get("time", 0)), "side": "buy" if t.get("isBuyerMaker") else "sell"} for t in data]

    async def _market_funding_rate(self, symbol: str) -> list[dict]:
        binance_symbol = _map_instrument(symbol)
        data = await self.get_funding_rate(binance_symbol)
        return [{"fundingRate": data.get("fundingRate", "0")}]

    async def _market_instruments(self, args: list[str]) -> dict:
        inst_id = None
        for i, a in enumerate(args):
            if a == "--instId" and i + 1 < len(args):
                inst_id = args[i + 1]
        if not inst_id:
            return {}
        binance_symbol = _map_instrument(inst_id)
        info = await self.get_exchange_info(binance_symbol)
        filters = {f["filterType"]: f for f in info.get("filters", [])}
        lot_filter = filters.get("LOT_SIZE", {})
        price_filter = filters.get("PRICE_FILTER", {})
        return {
            "instType": "SWAP" if _is_swap(inst_id) else "SPOT",
            "instId": inst_id,
            "ctVal": lot_filter.get("stepSize", "1"),
            "ctValCcy": "USDT",
            "lotSz": lot_filter.get("stepSize", "0.00001"),
            "minSz": lot_filter.get("minQty", "0.001"),
        }

    async def _trade_order(self, args: list[str]) -> dict:
        params = {}
        i = 2
        while i < len(args):
            if args[i] == "--instId" and i + 1 < len(args):
                params["instId"] = args[i + 1]
            elif args[i] == "--side" and i + 1 < len(args):
                params["side"] = args[i + 1]
            elif args[i] == "--ordType" and i + 1 < len(args):
                params["ordType"] = args[i + 1]
            elif args[i] == "--sz" and i + 1 < len(args):
                params["sz"] = args[i + 1]
            elif args[i] == "--px" and i + 1 < len(args):
                params["px"] = args[i + 1]
            elif args[i] == "--clOrdId" and i + 1 < len(args):
                params["clOrdId"] = args[i + 1]
            elif args[i] == "--reduceOnly":
                params["reduceOnly"] = True
            i += 1
        return await self.place_order(
            symbol=params.get("instId", ""), side=params.get("side", "buy"),
            quantity=params.get("sz", "0"),
            order_type="LIMIT" if params.get("ordType") == "l" else "MARKET",
            price=params.get("px"), reduce_only=params.get("reduceOnly", False),
            client_order_id=params.get("clOrdId"),
        )

    async def _trade_cancel(self, args: list[str]) -> dict:
        inst_id = order_id = None
        i = 2
        while i < len(args):
            if args[i] == "--instId" and i + 1 < len(args):
                inst_id = args[i + 1]
            elif args[i] == "--ordId" and i + 1 < len(args):
                order_id = args[i + 1]
            i += 1
        if inst_id and order_id:
            return await self.cancel_order(inst_id, order_id)
        return {}

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def _map_order_status(binance_status: str) -> str:
    mapping = {"NEW": "live", "PARTIALLY_FILLED": "partially_filled", "FILLED": "filled", "CANCELED": "canceled", "CANCELLED": "canceled", "REJECTED": "rejected", "EXPIRED": "canceled"}
    return mapping.get(binance_status.upper(), "live")
