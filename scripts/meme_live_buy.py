#!/usr/bin/env python3
"""Guided tiny-live buy for the Book-2 satellite (default: paper).

Usage (on the VPS):
    cd /opt/tars
    ./venv/bin/python scripts/meme_live_buy.py --mint <MINT> --usd 1.0
    ./venv/bin/python scripts/meme_live_buy.py --mint <MINT> --usd 1.0 \\
        --live --confirm "YES $1 <MINT> test"

Without --live, prints the quote + hurdle verdict and stops (paper).
With --live, requires --confirm (any non-empty operator string): quotes,
signs with /etc/tars/book2-satellite.json, executes via Jupiter, prints
the signature for Solscan verification. Caps + hurdle enforced either way.

Conventions: buys are SOL -> token (inputMint = wSOL). --usd is pre-swap
notional; lamports derive from the live SOL price (/price/v3).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WSOL = "So11111111111111111111111111111111111111112"
KEYPATH = "/etc/tars/book2-satellite.json"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tiny-live satellite buy (paper by default).")
    ap.add_argument("--mint", required=True, help="output token mint")
    ap.add_argument("--usd", type=float, required=True, help="pre-swap USD notional")
    ap.add_argument("--live", action="store_true", help="actually broadcast")
    ap.add_argument("--confirm", default="", help="operator confirm string (live only)")
    ap.add_argument("--slippage-bps", type=int, default=100)
    args = ap.parse_args(argv)

    from src.meme_execute import (
        ExecutionLedger,
        live_buy,
        token_prices,
    )

    prices = token_prices([WSOL])
    sol_usd = prices.get(WSOL)
    if not sol_usd:
        print("ABORT: no SOL price; cannot size lamports")
        return 2
    lamports = int(args.usd / sol_usd * 1_000_000_000)
    if lamports <= 0:
        print("ABORT: notional too small to express in lamports")
        return 2

    taker = None
    try:
        from src.meme_execute import book2_pubkey
        taker = book2_pubkey(KEYPATH)
    except Exception as e:
        print(f"ABORT: cannot read Book-2 pubkey ({e}); "
              f"solders installed? pip install solders")
        return 2
    # Cost estimate: Jupiter RTSE at order time is authoritative; pre-check
    # with venue flat proxy (1% + $0.50 gas) so the hurdle bites early.
    from src.meme_satellite import PAPER_GAS_USD
    est_costs = args.usd * 0.01 * 2 + PAPER_GAS_USD

    ledger = ExecutionLedger()
    out = live_buy(
        WSOL, args.mint, lamports, taker, KEYPATH, est_costs, args.usd,
        ledger, slippage_bps=args.slippage_bps,
        confirm=args.confirm if args.live else "",
    )
    if not out["broadcast"]:
        print(f"PAPER: {out['reason']} (allowed ${out['allowed_size']:.2f})")
        quote = out.get("quote", {})
        print(f"quote outAmount: {quote.get('outAmount')} "
              f"slippageBps: {quote.get('slippageBps')}")
        return 0
    receipt = out["receipt"]
    print(f"BROADCAST: signature {receipt.get('signature')}")
    print(f"verify: https://solscan.io/tx/{receipt.get('signature')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
