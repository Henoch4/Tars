"""Manual read-only Hyperliquid book-walk probe; network runs only via main()."""
import argparse
import json
from pathlib import Path

from scripts.hyperliquid_slippage import (
    get_l2_book, parse_l2_book, book_walk_execution, impact_bps_sqrt, mid_price,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=Path(__file__).resolve().parents[2] / "data/diagnostics/hl_bookwalk_results.json",
        help="JSON result file (default: ignored data/diagnostics directory)",
    )
    args = parser.parse_args()
    coins = ["BTC", "ETH", "SOL"]
    sizes_usd = [1000, 10000, 50000, 100000]
    sides = ["buy", "sell"]
    taker_fee_bps = 4.5  # Hyperliquid perp base taker fee

    results = {}
    for coin in coins:
        try:
            raw = get_l2_book(coin)
            book = parse_l2_book(raw)
            mid = mid_price(book)
            print(f"OK {coin}: mid={mid:.2f}, best_bid={book.bids[0].price:.2f}, best_ask={book.asks[0].price:.2f}")
            coin_results = {}
            for side in sides:
                side_results = {}
                for size in sizes_usd:
                    walk = book_walk_execution(size, side, book, taker_fee_bps)
                    comp = {
                        "vwap": walk.get("vwap_fill_price"),
                        "price_impact_bps": walk.get("price_impact_bps"),
                        "total_slippage_bps": walk.get("total_slippage_bps"),
                        "fully_filled": walk.get("fully_filled"),
                        "levels_consumed": walk.get("levels_consumed"),
                        "error": walk.get("error"),
                    }
                    # Also compute sqrt estimate for comparison
                    levels = book.asks if side == "buy" else book.bids
                    depth = 0.0
                    for lvl in levels:
                        lvl_usd = lvl.price * lvl.size
                        if depth + lvl_usd <= size * 10:
                            depth += lvl_usd
                        else:
                            depth += size * 10 - depth
                            break
                    sqrt_impact = impact_bps_sqrt(size, depth, 0.5)
                    comp["sqrt_impact_bps_k0.5"] = round(sqrt_impact, 4)
                    comp["depth_walked_usd"] = round(depth, 4)
                    side_results[str(size)] = comp
                coin_results[side] = side_results
            results[coin] = coin_results
        except Exception as e:
            print(f"FAIL {coin}: {e}")
            results[coin] = {"error": str(e)}

    print("\n=== BOOK-WALK EXECUTION ESTIMATES (live orderbook) ===")
    for coin, coin_data in results.items():
        if "error" in coin_data:
            continue
        print(f"\n{coin} (taker fee = {taker_fee_bps} bps):")
        for side, side_data in coin_data.items():
            for size_str, comp in side_data.items():
                if comp.get("error"):
                    print(f"  {side} ${int(size_str):,}: {comp['error']}")
                else:
                    print(f"  {side} ${int(size_str):,}: vwap={comp['vwap']:.2f}, "
                          f"impact={comp['price_impact_bps']:.2f}bps, "
                          f"total={comp['total_slippage_bps']:.2f}bps, "
                          f"levels={comp['levels_consumed']}, "
                          f"filled={comp['fully_filled']}, "
                          f"sqrt_k0.5={comp['sqrt_impact_bps_k0.5']:.2f}bps")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
