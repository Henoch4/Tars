"""Fresh-mint discovery + enrichment (Book 2) — DexScreener free API.

No key, no account: the public endpoints below are the discovery feed the
satellite polls on a slow cadence (30-60s; the edge is pickiness, not
speed). pump.fun runs no official launch API (its frontend board is
unofficial and gated) — DexScreener's documented free surface covers
both discovery (latest profiles, full of fresh ...pump mints) and
enrichment (liquidity, volume, txns, pair ages, graduated-venue check).

All HTTP goes through one injectable getter so tests run offline and a
DexScreener outage degrades to zero candidates (SKIP doctrine), never
to fabricated mints.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request

logger = logging.getLogger(__name__)

PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
TIMEOUT_S = 20

# Venues that only exist post-bonding-curve: a liquid pool on any of
# these is the graduation/survival confirmation the filter wants.
GRADUATED_VENUES = frozenset({"raydium", "orca", "meteora", "jupiter"})


class DiscoveryError(RuntimeError):
    """Typed discovery failure: zero candidates this round, never a guess."""


def _http_get(url: str, timeout_s: float = TIMEOUT_S) -> object:
    # DexScreener refuses Python-urllib's default User-Agent (HTTP 403 on the
    # box while curl passes) — identify as a browser client, nothing exotic.
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (TARS paper research loop)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        raise DiscoveryError(f"dexscreener GET failed: {type(e).__name__}") from e


def fetch_fresh_mints(limit: int = 25, get=None) -> list:
    """Latest-profile mints on Solana, pump.fun launches first (their
    addresses end in 'pump'), then everything else Solana. Returns
    [{mint, chain}] — never fabricates when the feed is down (raises)."""
    get = get or _http_get
    try:
        data = get(PROFILES_URL)
    except Exception as e:
        raise DiscoveryError(f"profiles feed failed: {e}") from e
    if not isinstance(data, list):
        raise DiscoveryError("profiles feed returned non-list")
    sol = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if entry.get("chainId") != "solana":
            continue
        mint = entry.get("tokenAddress")
        if isinstance(mint, str) and mint.strip():
            sol.append({"mint": mint.strip(), "chain": "solana"})
    sol.sort(key=lambda item: (not item["mint"].endswith("pump"), item["mint"]))
    seen, out = set(), []
    for item in sol:
        if item["mint"] not in seen:
            seen.add(item["mint"])
            out.append(item)
        if len(out) >= limit:
            break
    return out


def enrich_mint(mint: str, now_s: float | None = None, get=None) -> dict:
    """DexScreener enrichment for one mint: deepest SOL-quoted pool wins
    (best exit proxy). Returns liquidity_usd, volume_24h_usd, txns_24h,
    age_s (oldest pair), graduated (liquid pool on a post-curve venue),
    price_usd. Unknown/absent fields are None (filter SKIP-defaults).
    Fresh mints DexScreener hasn't indexed yet come back with explicit
    null pairs — that raises (too fresh to price), never zeros."""
    get = get or _http_get
    if not isinstance(mint, str) or not mint.strip():
        raise DiscoveryError(f"invalid mint {mint!r}")
    try:
        data = get(TOKEN_URL.format(mint=mint.strip()))
    except Exception as e:
        raise DiscoveryError(f"token lookup failed for {mint}: {e}") from e
    pairs = ((data or {}).get("pairs") or []) if isinstance(data, dict) else []
    sol_pairs = [p for p in pairs
                 if isinstance(p, dict) and p.get("chainId") == "solana"]
    if not sol_pairs:
        raise DiscoveryError(f"no solana pairs for {mint} (too fresh to index?)")
    now_ms = int((now_s if now_s is not None else time.time()) * 1000)

    def _liq(p):
        try:
            return float((p.get("liquidity") or {}).get("usd") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    best = max(sol_pairs, key=_liq)
    created = [
        c for c in (p.get("pairCreatedAt") for p in sol_pairs)
        if isinstance(c, (int, float))
    ]
    age_s = (now_ms - min(created)) / 1000.0 if created else None
    txns = (best.get("txns") or {}).get("h24") or {}
    graduated = any(
        p.get("dexId") in GRADUATED_VENUES and _liq(p) > 0 for p in sol_pairs
    )
    raw_price = best.get("priceUsd")
    try:
        price = float(raw_price) if raw_price is not None else None
    except (TypeError, ValueError):
        price = None
    raw_volume = (best.get("volume") or {}).get("h24")
    try:
        volume = float(raw_volume) if raw_volume is not None else None
    except (TypeError, ValueError):
        volume = None
    raw_mcap = best.get("marketCap")
    try:
        mcap = float(raw_mcap) if raw_mcap is not None else None
    except (TypeError, ValueError):
        mcap = None
    base = best.get("baseToken")
    symbol = base.get("symbol") if isinstance(base, dict) else None
    return {
        "mint": mint,
        "liquidity_usd": _liq(best) or None,
        "volume_24h_usd": volume,
        "txns_24h": txns if isinstance(txns, dict) else None,
        "age_s": age_s,
        "graduated": graduated,
        "price_usd": price,
        "venue": best.get("dexId"),
        "mcap_usd": mcap,
        "symbol": symbol or None,
        # Best-pool address: excluded from concentration math downstream
        # (curve/pool vaults are program custody, not organic holders).
        "pool_address": best.get("pairAddress"),
    }


def discover_candidates(limit: int = 25, get=None) -> list:
    """One discovery round: fresh mints, Solana only. Failures raise
    DiscoveryError (caller records zero candidates, never guesses)."""
    return fetch_fresh_mints(limit=limit, get=get)
