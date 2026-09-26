"""Yahoo Finance chart adapter — underlying equity daily bars + dividends.

Public chart API, no key: GET query1.finance.yahoo.com/v8/finance/chart/{SYM}
params: interval=1d, range=1y, events=div,split.
Shape: {"chart": {"result": [{"timestamp": [...], "indicators": {
  "quote": [{"close": [...], "open": [...], ...}],
  "adjclose": [{"adjclose": [...]}]}, "events": {"dividends": {ts: {"amount", "date"}}}}}]}}

Role: backtest substrate for carry ECONOMICS (dividends + spot drift).
The token leg has no public history (xStocks price-data is live-only,
verified 404s 2026-09-20; DBC pools are days old) — token-vs-spot basis is
measured live (xstocks vs bitget spread) and charged as execution cost,
not backtested. Stated plainly in docs/BACKTEST.md.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE = "https://query1.finance.yahoo.com"
UNDERLYING = {"AAPLx": "AAPL", "TSLAx": "TSLA", "NVDAx": "NVDA"}
TIMEOUT = 25.0


class YahooClient:
    def __init__(self, base: str = BASE, timeout: float = TIMEOUT):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def daily(self, symbol: str, range_: str = "1y") -> dict[str, Any]:
        """Daily closes + dividend events for an xStocks symbol's underlying."""
        underlying = UNDERLYING.get(symbol, symbol)
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        resp = await self._client.get(
            f"{self.base}/v8/finance/chart/{underlying}",
            params={"interval": "1d", "range": range_, "events": "div,split"},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        res = resp.json()["chart"]["result"][0]
        ts = res.get("timestamp") or []
        q = (res.get("indicators") or {}).get("quote", [{}])[0]
        closes = q.get("close") or []
        bars = [
            {"t": t, "close": c}
            for t, c in zip(ts, closes)
            if t is not None and c is not None
        ]
        divs = []
        for k, v in ((res.get("events") or {}).get("dividends") or {}).items():
            divs.append({"t": int(v.get("date", k)), "amount": float(v.get("amount", 0))})
        return {"symbol": symbol, "underlying": underlying, "bars": bars,
                "dividends": sorted(divs, key=lambda d: d["t"]),
                "fetched_at": time.time()}
