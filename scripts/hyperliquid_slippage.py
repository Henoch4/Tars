#!/usr/bin/env python3
"""Hyperliquid orderbook slippage model — Phase 9 research (read-only).

Two INDEPENDENT estimates, kept separate:

A. ORDERBOOK EXECUTION ESTIMATE (book-walk VWAP)
   Walk the actual asks (buy) or bids (sell) from the live orderbook,
   filling the requested order size level-by-level. Compute volume-weighted
   average fill price vs mid-price reference. Fees added separately.
   This answers: "if I hit the market right now, what fill do I get?"

B. EMPIRICAL IMPACT ESTIMATE (square-root model)
   impact_bps = k * sqrt(size_usd / depth_usd) * 1e4
   Kept separate from book-walk. Coefficient k must be calibrated against
   observed execution costs — not treated as verified.

No keys, no state changes, no live trading. Public info endpoint only.
Phase 9 research — capital deployment remains blocked.
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"

# Hyperliquid base fee schedule.
# Actual fees vary with account volume tier, staking discounts, and
# other applicable fee adjustments. These are BASE rates — configure
# per market and account before using for live estimates.
BASE_TAKER_FEE_BPS = {
    "perpetuals": 4.5,
    "spot": 7.0,
}


def _post_info(payload: dict, timeout: float = 15.0) -> dict:
    """POST to Hyperliquid info endpoint (read-only public API)."""
    req = urllib.request.Request(
        HYPERLIQUID_INFO_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def get_l2_book(coin: str) -> dict:
    """Fetch L2 orderbook snapshot for a coin (bids + asks)."""
    return _post_info({"type": "l2Book", "coin": coin})


@dataclass
class BookLevel:
    price: float
    size: float  # in coin units


@dataclass
class OrderBook:
    coin: str
    bids: list[BookLevel]  # sorted desc by price
    asks: list[BookLevel]  # sorted asc by price
    ts: float


def parse_l2_book(raw: dict) -> OrderBook:
    """Parse Hyperliquid l2Book response into OrderBook."""
    coin = raw.get("coin", "")
    levels = raw.get("levels", [[], []])
    bids_raw, asks_raw = levels[0], levels[1]
    bids = [BookLevel(float(l["px"]), float(l["sz"])) for l in bids_raw]
    asks = [BookLevel(float(l["px"]), float(l["sz"])) for l in asks_raw]
    bids.sort(key=lambda l: l.price, reverse=True)
    asks.sort(key=lambda l: l.price)
    return OrderBook(
        coin=coin,
        bids=bids,
        asks=asks,
        ts=time.time(),
    )


def mid_price(book: OrderBook) -> float | None:
    """Mid price = (best_bid + best_ask) / 2. Reference for impact."""
    if not book.bids or not book.asks:
        return None
    return (book.bids[0].price + book.asks[0].price) / 2.0


def walk_book_for_size(
    levels: list[BookLevel], side: str, size_usd: float
) -> dict:
    """Walk book levels filling `size_usd` of notional.

    side "buy"  -> walk asks (pay more, fill at ask prices)
    side "sell" -> walk bids (receive less, fill at bid prices)

    Returns dict with filled notional, VWAP fill price, levels consumed,
    and whether the book had sufficient depth.
    """
    remaining_usd = size_usd
    filled_usd = 0.0
    weighted_price_sum = 0.0  # sum(price * size_filled_usd)
    levels_consumed = 0
    levels_detail = []

    for lvl in levels:
        if remaining_usd <= 0:
            break
        lvl_usd = lvl.price * lvl.size
        fill_usd = min(lvl_usd, remaining_usd)
        fill_size = fill_usd / lvl.price if lvl.price > 0 else 0.0

        filled_usd += fill_usd
        weighted_price_sum += lvl.price * fill_usd
        remaining_usd -= fill_usd
        levels_consumed += 1
        levels_detail.append({
            "price": lvl.price,
            "level_size": lvl.size,
            "fill_usd": fill_usd,
            "fill_size": fill_size,
        })

        if remaining_usd <= 1e-9:
            break

    vwap = weighted_price_sum / filled_usd if filled_usd > 0 else 0.0
    fully_filled = remaining_usd <= 1e-9

    return {
        "filled_usd": filled_usd,
        "requested_usd": size_usd,
        "fully_filled": fully_filled,
        "unfilled_usd": remaining_usd,
        "vwap_fill_price": vwap,
        "levels_consumed": levels_consumed,
        "levels_detail": levels_detail,
    }


def book_walk_execution(
    size_usd: float,
    side: str,  # "buy" or "sell"
    book: OrderBook,
    taker_fee_bps: float = 4.5,  # Hyperliquid perp base taker fee
) -> dict:
    """Orderbook execution estimate (book-walk VWAP + fees).

    side "buy"  -> hit asks, pay vwap >= best_ask
    side "sell" -> hit bids, receive vwap <= best_bid

    Impact measured vs mid price: (vwap - mid) / mid * 1e4 for buy,
    (mid - vwap) / mid * 1e4 for sell.
    """
    mid = mid_price(book)
    if mid is None:
        return {"error": "no valid mid price", "coin": book.coin}

    levels = book.asks if side == "buy" else book.bids
    best_price = levels[0].price if levels else 0.0

    walk = walk_book_for_size(levels, side, size_usd)

    if not walk["fully_filled"]:
        return {
            "coin": book.coin,
            "side": side,
            "size_usd": size_usd,
            "error": f"insufficient depth: filled {walk['filled_usd']:.2f} of {size_usd:.2f} USD",
            "mid_price": mid,
            "best_price": best_price,
            "taker_fee_bps": taker_fee_bps,
            "ts": book.ts,
        }

    vwap = walk["vwap_fill_price"]

    # Price impact vs mid price (bps)
    if side == "buy":
        # Buying above mid = adverse impact
        price_impact_bps = (vwap - mid) / mid * 1e4
    else:
        # Selling below mid = adverse impact
        price_impact_bps = (mid - vwap) / mid * 1e4

    price_impact_bps = max(0.0, price_impact_bps)
    total_bps = price_impact_bps + taker_fee_bps

    return {
        "coin": book.coin,
        "side": side,
        "size_usd": size_usd,
        "mid_price": round(mid, 6),
        "best_price": round(best_price, 6),
        "vwap_fill_price": round(vwap, 6),
        "filled_usd": round(walk["filled_usd"], 4),
        "fully_filled": walk["fully_filled"],
        "price_impact_bps": round(price_impact_bps, 4),
        "taker_fee_bps": taker_fee_bps,
        "total_slippage_bps": round(total_bps, 4),
        "levels_consumed": walk["levels_consumed"],
        "levels_detail": walk["levels_detail"],
        "ts": book.ts,
    }


def impact_bps_sqrt(size_usd: float, depth_usd: float, k: float = 0.5) -> float:
    """Square-root empirical impact estimate (SEPARATE from book-walk).

    impact_bps = k * sqrt(size / depth) * 1e4

    Coefficient k must be calibrated against observed execution costs.
    Do NOT treat as verified property of the CLOB.
    """
    if depth_usd <= 0:
        return float("inf")
    ratio = size_usd / depth_usd
    if ratio >= 1.0:
        return 10000.0
    return k * math.sqrt(ratio) * 1e4


def depth_usd_walk(levels: list[BookLevel], max_usd: float) -> float:
    """Walk book levels accumulating USD depth until max_usd."""
    accumulated = 0.0
    for lvl in levels:
        lvl_usd = lvl.price * lvl.size
        room = max_usd - accumulated
        if lvl_usd <= room:
            accumulated += lvl_usd
        else:
            accumulated += room
            break
    return accumulated


def compare_models(
    size_usd: float,
    side: str,
    book: OrderBook,
    taker_fee_bps: float = 4.5,
    k: float = 0.5,
) -> dict:
    """Compare book-walk execution vs square-root empirical estimate."""
    levels = book.asks if side == "buy" else book.bids
    best_price = levels[0].price if levels else 0.0

    # Book-walk execution (A)
    walk = book_walk_execution(size_usd, side, book, taker_fee_bps)

    # Empirical estimate (B) — uses available depth on the relevant side
    depth = depth_usd_walk(levels, size_usd * 10)  # walk 10x for depth estimate
    sqrt_impact = impact_bps_sqrt(size_usd, depth, k)

    return {
        "coin": book.coin,
        "side": side,
        "size_usd": size_usd,
        "mid_price": round(book.bids[0].price + (book.asks[0].price - book.bids[0].price) / 2, 6) if book.bids and book.asks else None,
        "best_price": round(best_price, 6),
        "book_walk": {
            "vwap_fill_price": walk.get("vwap_fill_price"),
            "price_impact_bps": walk.get("price_impact_bps"),
            "total_slippage_bps": walk.get("total_slippage_bps"),
            "depth_usd": walk.get("filled_usd"),  # actually filled, not total depth
            "levels_consumed": walk.get("levels_consumed"),
            "fully_filled": walk.get("fully_filled"),
            "error": walk.get("error"),
        },
        "sqrt_estimate": {
            "impact_bps": round(sqrt_impact, 4),
            "k": k,
            "depth_usd": round(depth, 4),
            "note": "empirical estimate — k must be calibrated against realized fills",
        },
        "ts": book.ts,
    }


def fetch_and_compare(coin: str, size_usd: float, side: str,
                      taker_fee_bps: float = 4.5, k: float = 0.5) -> dict:
    """Fetch live book and compare models."""
    raw = get_l2_book(coin)
    book = parse_l2_book(raw)
    return compare_models(size_usd, side, book, taker_fee_bps, k)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Hyperliquid orderbook slippage model (read-only research)")
    ap.add_argument("--coin", default="BTC", help="Hyperliquid coin symbol")
    ap.add_argument("--size-usd", type=float, default=10000.0, help="Order size in USD")
    ap.add_argument("--side", default="buy", choices=["buy", "sell"], help="Order side")
    ap.add_argument("--taker-fee-bps", type=float, default=4.5, help="Taker fee in bps (perp base=4.5, spot=7.0)")
    ap.add_argument("--k", type=float, default=0.5, help="Square-root impact coefficient k")
    ap.add_argument("--fetch-live", action="store_true", help="Fetch live book from Hyperliquid API")
    args = ap.parse_args()

    if args.fetch_live:
        try:
            result = fetch_and_compare(args.coin, args.size_usd, args.side,
                                       args.taker_fee_bps, args.k)
            print(json.dumps(result, indent=2))
        except Exception as e:
            print(f"Live fetch failed: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Use --fetch-live to query Hyperliquid info endpoint.")
        print("Two estimates: book-walk VWAP (execution) and square-root (empirical, needs k calibration).")
        print(f"Default taker fee = {4.5} bps (Hyperliquid perpetuals base).")
