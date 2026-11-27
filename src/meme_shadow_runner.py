"""Read-only observer for the main memecoin satellite."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from .meme_discovery import DiscoveryError, fetch_fresh_mints
from .meme_satellite import TokenSnapshot, scream_filter
from .meme_feed import HeliusFeed, FeedError
from .meme_rugcheck import rugcheck_mint, RugCheckError
from .meme_discovery import enrich_mint
from .meme_jupiter import round_trip_cost_pct

JUPITER_PRICE = "https://api.jup.ag/price/v3"
SOL_MINT = "So11111111111111111111111111111111111111112"


def _token_prices(mints: list[str]) -> dict:
    clean = [m for m in mints if isinstance(m, str) and m.strip()]
    if not clean:
        return {}
    req = urllib.request.Request(
        JUPITER_PRICE + "?ids=" + urllib.parse.quote(",".join(clean)),
        headers={"Accept": "application/json", "User-Agent": "TARS-shadow/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        body = json.loads(response.read().decode())
    prices: dict[str, float] = {}
    for mint in clean:
        raw = (body.get(mint) or {}).get("usdPrice")
        if isinstance(raw, (int, float, str)):
            try:
                prices[mint] = float(raw)
            except (TypeError, ValueError):
                continue
    return prices


def _screen_readonly(mint: str, sol_price: float):
    try:
        extra = enrich_mint(mint)
        snapshot = HeliusFeed().snapshot_for_mint(mint, extra)
        try:
            snapshot.rugcheck_rugged = rugcheck_mint(mint).get("rugcheck_rugged")
        except RugCheckError:
            pass
        try:
            snapshot.round_trip_cost_pct = round_trip_cost_pct(mint, price_usd=sol_price)
        except Exception:  # noqa: BLE001
            pass
        return snapshot
    except (DiscoveryError, FeedError):
        return None


def append_rows(path: str | Path, rows: list[dict]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def observe_once(*, limit: int = 25,
                 output: str | Path = "data/meme_satellite/shadow_observations.jsonl",
                 discover_fn: Callable[[int], list] = fetch_fresh_mints,
                 price_fn: Callable[[list[str]], dict] = _token_prices,
                 screen_fn: Callable[[str, float], TokenSnapshot | None] = _screen_readonly,
                 now: Callable[[], float] = time.time,
                 on_snapshot: Callable[[TokenSnapshot, float], None] | None = None) -> dict:
    """Record one discovery/enrichment/filter round; failures become rows."""
    started = now()
    try:
        sol_price = price_fn([SOL_MINT]).get(SOL_MINT)
    except Exception as exc:  # noqa: BLE001
        row = {"event": "shadow_error", "stage": "sol_price", "error": type(exc).__name__, "ts": now()}
        append_rows(output, [row])
        return row
    if not sol_price:
        row = {"event": "shadow_error", "stage": "sol_price", "error": "unavailable", "ts": now()}
        append_rows(output, [row])
        return row
    try:
        candidates = discover_fn(limit)
    except DiscoveryError as exc:
        row = {"event": "shadow_error", "stage": "discovery", "error": type(exc).__name__, "reason": str(exc), "ts": now()}
        append_rows(output, [row])
        return row
    except Exception as exc:  # noqa: BLE001
        row = {"event": "shadow_error", "stage": "discovery", "error": type(exc).__name__, "ts": now()}
        append_rows(output, [row])
        return row

    rows: list[dict] = []
    for candidate in candidates:
        mint = candidate.get("mint") if isinstance(candidate, dict) else None
        if not isinstance(mint, str) or not mint.strip():
            rows.append({"event": "candidate_rejected", "reason": "invalid_mint", "candidate": candidate, "ts": now()})
            continue
        try:
            snapshot = screen_fn(mint, float(sol_price))
            if snapshot is None:
                rows.append({"event": "candidate_rejected", "mint": mint, "reason": "enrichment_failed", "ts": now()})
                continue
            verdict = scream_filter(snapshot)
            if on_snapshot is not None:
                on_snapshot(snapshot, now())
            rows.append({"event": "candidate_observed", "mint": mint,
                         "symbol": getattr(snapshot, "symbol", None),
                         "passed": bool(verdict.passed),
                         "rejected_by": verdict.rejected_by,
                         "reasons": verdict.reasons,
                         "liquidity_usd": getattr(snapshot, "liquidity_usd", None),
                         "mcap_usd": getattr(snapshot, "mcap_usd", None), "ts": now()})
        except Exception as exc:  # noqa: BLE001
            rows.append({"event": "candidate_error", "mint": mint, "error": type(exc).__name__, "ts": now()})
    summary = {"event": "shadow_round", "candidates": len(candidates),
               "observed": sum(r.get("event") == "candidate_observed" for r in rows),
               "passed": sum(r.get("passed") is True for r in rows),
               "rejected": sum(r.get("event") == "candidate_rejected" or r.get("passed") is False for r in rows),
               "elapsed_s": round(now() - started, 3), "ts": now()}
    append_rows(output, rows + [summary])
    return summary
