"""Solami Blur firehose client (Data API) — decoded Solana market events.

Blur pushes decoded events over a WebSocket as they confirm (block-time
latency, not poll time): token_create, pool_create, graduation, plus
launches/graduating/graduated list snapshots on connect and meme/surge/
trending/metadata auxiliaries. This module consumes the shapes exactly
as the dashboard documents them — no guessed fields.

Auth: API key with the DataApi permission, passed as ?api_key= (browsers
cannot set headers). Keys without the role are rejected — surfaced as
BlurError, never silent.

Design rules (same doctrine as the rest of the satellite):
- connect timeout + overall collection budget: a hung socket aborts the
  round instead of hanging the loop.
- Unknown event types / malformed payloads are SKIPPED with a count, not
  fatal and never fabricated into mints.
- Empty filter would be the full firehose: this client always subscribes
  narrowly (launches + creations + graduations), the satellite's needs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)

BLUR_WS_TEMPLATE = "wss://ws.solami.dev/data/subscribe?chain=solana&api_key={key}"

# Event types this client consumes. Everything else is counted + skipped.
WANT_TYPES = frozenset({
    "token_create", "pool_create", "graduation",
    "launches", "graduating", "graduated",
})

# List snapshots arrive as {type: "launches", items: [...]} (and siblings).
LIST_TYPES = {"launches": "launches", "graduating": "graduating",
              "graduated": "graduated"}

# Quote/base mints that are never trade candidates: a pool_create for a
# SOL-paired pool reports the wSOL side under "mint". Enriching wSOL
# triggers unbounded RPC scans (Helius: "Too many accounts requested").
# USDC/USDT included for the same reason on stable-paired pools.
QUOTE_MINTS = frozenset({
    "So11111111111111111111111111111111111111112",  # wSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
})


class BlurError(RuntimeError):
    """Typed Blur failure: zero candidates this round, never a guess."""


def build_subscribe_message(types: list | None = None,
                            backfill: int = 50) -> str:
    """Subscribe payload: narrow types + bounded backfill. backfill 0-200
    replays recent events per type (tagged backfill:true); list snapshots
    always arrive on connect regardless."""
    if not 0 <= backfill <= 200:
        raise ValueError(f"backfill must be 0-200, got {backfill!r}")
    return json.dumps({
        "types": list(types) if types else sorted(WANT_TYPES - set(LIST_TYPES)),
        "backfill": backfill,
    })


def parse_launch_event(msg: dict) -> list:
    """One decoded WS message -> candidate mint dicts (possibly empty).
    Handles single events (token_create/pool_create/graduation with a mint
    field) and list snapshots ({type, items:[{mint,...}, ...]}). Unknown
    shapes return [] — skipped upstream, never fabricated."""
    if not isinstance(msg, dict):
        return []
    kind = msg.get("type")
    if kind in LIST_TYPES:
        # List snapshots carry rows under "items" (launches) or "rows"
        # (graduating/graduated) depending on the feed version — accept
        # either, never assume. Row dicts need a non-empty string mint;
        # quote mints (wSOL side of a pool row) are excluded upstream.
        items = msg.get("items")
        if not isinstance(items, list):
            items = msg.get("rows")
        if not isinstance(items, list):
            return []
        out = []
        for it in items:
            if (isinstance(it, dict) and isinstance(it.get("mint"), str)
                    and it["mint"] and it["mint"] not in QUOTE_MINTS):
                out.append({"mint": it["mint"], "via": kind,
                            "name": it.get("name"), "symbol": it.get("symbol")})
        return out
    if kind in ("token_create", "pool_create", "graduation"):
        mint = msg.get("mint")
        if isinstance(mint, str) and mint and mint not in QUOTE_MINTS:
            return [{"mint": mint, "via": kind,
                     "name": msg.get("name"), "symbol": msg.get("symbol"),
                     "backfill": bool(msg.get("backfill", False))}]
    return []


async def collect_blur_candidates(api_key: str,
                                   url_template: str = BLUR_WS_TEMPLATE,
                                   duration_s: float = 60.0,
                                   connect_timeout_s: float = 15.0,
                                   backfill: int = 50,
                                   connect_fn=None,
                                   on_raw=None) -> dict:
    """Open the firehose, collect for duration_s, close. Returns
    {"mints": [...deduped...], "counts": {type: n}, "skipped": n,
     "unparsed": {type: n}}.
    connect_fn(url, subscribe_msg) override exists for hermetic tests
    (yields parsed dicts); production uses websockets.connect.
    on_raw(msg), when given, sees every decoded dict (probe/diagnosis;
    must never raise — guarded). unparsed counts WANT-type messages that
    yielded zero candidates: the exact evaporation this counter exists to
    catch (e.g. list shapes whose items carry no "mint" key)."""
    if not api_key or not api_key.strip():
        raise BlurError("Blur needs an API key with the DataApi role")
    store: dict = {"mints": [], "counts": {}, "skipped": 0, "unparsed": {}}
    seen: set = set()

    def _ingest(msg: dict):
        store["counts"][str(msg.get("type"))] = \
            store["counts"].get(str(msg.get("type")), 0) + 1
        if on_raw is not None:
            try:
                on_raw(msg)
            except Exception:
                pass
        found = parse_launch_event(msg)
        if not found and str(msg.get("type")) not in WANT_TYPES:
            store["skipped"] += 1
        if not found and str(msg.get("type")) in WANT_TYPES:
            t = str(msg.get("type"))
            store["unparsed"][t] = store["unparsed"].get(t, 0) + 1
        for cand in found:
            if cand["mint"] not in seen:
                seen.add(cand["mint"])
                store["mints"].append(cand)

    if connect_fn is not None:
        async for msg in connect_fn():
            _ingest(msg)
        return store

    try:
        from websockets.asyncio.client import connect as ws_connect
    except Exception as e:
        raise BlurError(f"websockets lib unavailable: {e}") from e
    url = url_template.format(key=api_key)
    sub_msg = build_subscribe_message(backfill=backfill)
    deadline = time.time() + duration_s
    try:
        async with asyncio.timeout(connect_timeout_s + duration_s + 5):
            async with ws_connect(url, open_timeout=connect_timeout_s) as ws:
                await ws.send(sub_msg)
                while time.time() < deadline:
                    try:
                        raw = await asyncio.wait_for(
                            ws.recv(), timeout=max(1.0, deadline - time.time()))
                    except asyncio.TimeoutError:
                        break
                    try:
                        msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                    except (ValueError, TypeError):
                        store["skipped"] += 1
                        continue
                    if not isinstance(msg, dict):
                        store["skipped"] += 1
                        continue
                    _ingest(msg)
    except BlurError:
        raise
    except Exception as e:
        raise BlurError(f"blur stream failed: {type(e).__name__}: {e}") from e
    return store
