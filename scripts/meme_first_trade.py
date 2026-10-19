"""First live round trip: Book-2 wallet, SOL -> USDC, paper-size.

Usage (on the VPS):
    ./venv/bin/python scripts/meme_first_trade.py quote   # numbers only, $0 moves
    ./venv/bin/python scripts/meme_first_trade.py go      # executes for real

The `go` form is the deliberate second act; without it nothing signs.
Prints quote -> hurdle verdict -> (if go) signature + final balance.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TAKER = "38hpURXXM1CM9Ugo4aWYkckM4H36jH2BXX5wZWz2MbzY"
KEYPATH = "/etc/tars/book2-satellite.json"
AMOUNT_RAW = 5_000_000  # 0.005 SOL (~$0.55): the paper-size live proof


def _sol_price_usd():
    from src.meme_execute import token_prices
    p = token_prices([WSOL])
    return p.get(WSOL)


def main(argv):
    from src.meme_execute import (ExecutionLedger, SwapError, live_buy,
                                  quote_swap)

    mode = argv[1] if len(argv) > 1 else "quote"
    if mode not in ("quote", "go"):
        print("usage: meme_first_trade.py [quote|go]")
        return 2

    # Get the quote FIRST: it carries actual fees + the implied SOL price
    # (outAmount/inAmount). No separate price call, no flat fee proxy —
    # the $0.50 memecoin-grade proxy wrongly dominated a $0.55 liquid-pair
    # size and the hurdle (correctly) refused it.
    try:
        order = quote_swap(WSOL, USDC, AMOUNT_RAW, TAKER)
    except SwapError as e:
        print("QUOTE FAILED:", e)
        return 1
    out_raw = int(order.get("outAmount") or 0)
    if out_raw <= 0:
        print("QUOTE FAILED: no outAmount")
        return 1
    implied_sol_usd = (out_raw / 1e6) / (AMOUNT_RAW / 1e9)  # USDC(6dp)/SOL(9dp)
    size_usd = 0.005 * implied_sol_usd

    # Actual fees from the quote response (platformFee + PriorityFeeInSol etc.)
    # Jupiter's fee structure is pair-dependent; the response carries the
    # components. Sum what's present, in SOL terms, convert via implied price.
    fee_sol = 0.0
    for k in ("PriorityFeeInSol", "priorityFeeInSol"):
        try:
            fee_sol += float(order.get(k) or 0) / 1e9
        except (TypeError, ValueError):
            pass
    try:
        platform_fee = float(order.get("feeAmount") or 0) / 1e9
        fee_sol += platform_fee
    except (TypeError, ValueError):
        pass
    fees_usd = fee_sol * implied_sol_usd
    # Slippage allowance (1% collar on the size, not a memecoin-grade proxy)
    costs = fees_usd + size_usd * 0.01

    print(f"pair:       0.005 SOL -> USDC")
    print(f"implied:    1 SOL = ${implied_sol_usd:.2f}")
    print(f"size:       ${size_usd:.2f}")
    print(f"fees(quote): ${fees_usd:.4f}")
    print(f"costs est:  ${costs:.4f} (hurdle allows <= ${size_usd * 0.10:.4f})")
    print(f"out:        {out_raw} raw USDC = ${out_raw / 1e6:.4f}")

    ledger = ExecutionLedger(max_session_usd=5.0)
    confirm = "GO first live trade" if mode == "go" else ""
    try:
        out = live_buy(WSOL, USDC, AMOUNT_RAW, TAKER, KEYPATH,
                       costs, size_usd, ledger, confirm=confirm)
    except SwapError as e:
        print("SWAP FAILED (typed):", e)
        return 1

    if not out.get("broadcast"):
        print("not broadcast:", out.get("reason"))
        return 0

    receipt = out["receipt"]
    print("BROADCAST: signature", receipt.get("signature"))
    print("in:", receipt.get("totalInputAmount"),
          "out:", receipt.get("totalOutputAmount"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
