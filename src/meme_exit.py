"""Live exit engine for Book-2 satellite positions.

Price monitor + stop-loss + profit ratchet + live sell execution.
Exit discipline mirrors meme_satellite.py constants so the sell rules
match the buy rules:

- EXIT_PRICE_DROP_PCT = 25.0     stop-loss from peak
- PROFIT_RATCHET_TRIGGER_GAIN_PCT = 25.0   arm ratchet at +25% peak gain
- PROFIT_RATCHET_TIGHTENED_DROP_PCT = 10.0 then trail at -10% off peak
- MAX_HOLD_S = 7 * 24 * 3600     hard time box

State lives in a JSON position file (default data/meme_satellite/live_position.json).
Paper by default: `check` never broadcasts. `sell` requires an explicit
confirm string (same two-act guard as the buy path).
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.meme_satellite import (
    EXIT_PRICE_DROP_PCT,
    PROFIT_RATCHET_TRIGGER_GAIN_PCT,
    PROFIT_RATCHET_TIGHTENED_DROP_PCT,
    MAX_HOLD_S,
)
from src.meme_execute import (
    SOL_MINT,
    live_sell,
    book2_pubkey,
    token_prices,
)


# --- config ----------------------------------------------------------------

STATE_PATH = Path(
    os.getenv("MEME_POSITION_STATE",
              "data/meme_satellite/live_position.json"))

KEYPATH_DEFAULT = "/etc/tars/book2-satellite.json"


def resolve_keypath(cli_value: Optional[str]) -> str:
    if cli_value:
        return cli_value
    from_env = (os.getenv("MEME_KEYPAIR_PATH") or "").strip()
    if from_env:
        return from_env
    return KEYPATH_DEFAULT


# --- position state --------------------------------------------------------

@dataclass
class Position:
    mint: str
    symbol: str
    entry_price_usd: float
    entry_sol: float
    entry_ts: float
    peak_price_usd: float
    token_amount: float        # human units (decimals applied)
    decimals: int
    ratchet_armed: bool = False
    sell_executed: bool = False


def load_position() -> Optional[Position]:
    if not STATE_PATH.exists():
        return None
    try:
        d = json.loads(STATE_PATH.read_text())
        return Position(**d)
    except Exception:
        return None


def save_position(pos: Position) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(asdict(pos), indent=2))


def create_position(mint: str, symbol: str, entry_sol: float,
                    token_amount: float, decimals: int,
                    entry_price_usd: Optional[float] = None) -> Position:
    now = time.time()
    if entry_price_usd is None:
        sol_price = token_prices([SOL_MINT]).get(SOL_MINT)
        if not sol_price:
            raise RuntimeError("cannot get SOL price for entry valuation")
        entry_usd = entry_sol * sol_price
        entry_price_usd = entry_usd / token_amount if token_amount > 0 else 0.0
    return Position(
        mint=mint, symbol=symbol,
        entry_price_usd=entry_price_usd,
        entry_sol=entry_sol,
        entry_ts=now,
        peak_price_usd=entry_price_usd,
        token_amount=token_amount,
        decimals=decimals,
    )


# --- market data -----------------------------------------------------------

def get_price(mint: str) -> Optional[float]:
    prices = token_prices([mint])
    return prices.get(mint)


# --- exit logic ------------------------------------------------------------

def check_exit(pos: Position, current_price: float) -> tuple[bool, str]:
    """Return (should_sell, reason). Mutates + saves peak/ratchet state."""
    now = time.time()

    # Update peak
    if current_price > pos.peak_price_usd:
        pos.peak_price_usd = current_price
        save_position(pos)

    # Arm profit ratchet
    if not pos.ratchet_armed:
        gain_pct = (current_price / pos.entry_price_usd - 1) * 100
        if gain_pct >= PROFIT_RATCHET_TRIGGER_GAIN_PCT:
            pos.ratchet_armed = True
            save_position(pos)
            return False, (f"ratchet armed: +{gain_pct:.1f}% gained, "
                           f"trailing -{PROFIT_RATCHET_TIGHTENED_DROP_PCT:.0f}% "
                           f"from peak armed")

    # Stop-loss: drop from peak
    drop_from_peak_pct = (
        (pos.peak_price_usd - current_price) / pos.peak_price_usd * 100
    )
    if drop_from_peak_pct >= EXIT_PRICE_DROP_PCT:
        return True, (f"stop-loss: {drop_from_peak_pct:.1f}% below peak "
                      f"${pos.peak_price_usd:.6f}")

    # Profit ratchet: once armed, trail at tightened drop off peak
    if pos.ratchet_armed:
        trail_drop_pct = (
            (pos.peak_price_usd - current_price) / pos.peak_price_usd * 100
        )
        if trail_drop_pct >= PROFIT_RATCHET_TIGHTENED_DROP_PCT:
            return True, (f"profit ratchet: {trail_drop_pct:.1f}% below "
                          f"armed peak ${pos.peak_price_usd:.6f}")

    # Hard time box: 7 days
    hold_h = (now - pos.entry_ts) / 3600
    if hold_h >= MAX_HOLD_S / 3600:
        return True, f"time box: held {hold_h:.1f}h (> {MAX_HOLD_S/3600:.0f}h)"

    return False, (f"hold: ${current_price:.6f} (peak ${pos.peak_price_usd:.6f}, "
                   f"drop {drop_from_peak_pct:.1f}%, "
                   f"ratchet {'armed' if pos.ratchet_armed else 'disarmed'})")


# --- sell execution --------------------------------------------------------

def get_token_balance(mint: str, owner: str) -> tuple[Optional[float], Optional[int]]:
    """Query Solana RPC for token account balance. Returns (human_units, decimals)."""
    import urllib.request
    import urllib.parse
    import json as _json

    payload = _json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [owner,
                   {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                   {"encoding": "jsonParsed"}],
    }).encode()

    req = urllib.request.Request(
        "https://api.mainnet-beta.solana.com",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = _json.loads(resp.read().decode())

    accounts = data.get("result", {}).get("value", [])
    for acc in accounts:
        info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        if info.get("mint") == mint:
            tok = info.get("tokenAmount", {})
            ui = tok.get("uiAmount")
            dec = tok.get("decimals")
            if ui is not None:
                return float(ui), int(dec) if dec is not None else 6
    return None, None


def execute_sell(pos: Position, confirm: str,
                 keypath: Optional[str] = None,
                 slippage_bps: int = 100) -> dict:
    """Sell entire position via Jupiter live path. Returns receipt dict."""
    if pos.sell_executed:
        return {"broadcast": False, "reason": "already sold", "position": pos.mint}

    kp = resolve_keypath(keypath)
    taker = book2_pubkey(kp)
    amount_raw = int(pos.token_amount * (10 ** pos.decimals))

    out = live_sell(
        pos.mint, SOL_MINT, amount_raw, taker, kp,
        slippage_bps=slippage_bps, confirm=confirm,
    )
    if out.get("broadcast"):
        pos.sell_executed = True
        save_position(pos)
    return out


# --- CLI -------------------------------------------------------------------

def cmd_init(mint: str, entry_sol: float, owner: Optional[str] = None) -> int:
    """Initialize a position from current on-chain token balance."""
    if owner is None:
        owner = book2_pubkey(resolve_keypath(None))
    amount, decimals = get_token_balance(mint, owner)
    if amount is None or amount <= 0:
        print(f"ABORT: no token account found for {mint} under {owner}")
        return 1
    pos = create_position(mint, mint, entry_sol, amount, decimals or 6)
    save_position(pos)
    print(f"Position initialized: {pos.symbol} ({pos.mint[:14]}...)")
    print(f"  entry ${pos.entry_price_usd:.6f}/token  "
          f"tokens={pos.token_amount:,.4f}  entry_sol={pos.entry_sol:.6f}")
    print(f"  state: {STATE_PATH}")
    return 0


def cmd_check() -> int:
    """Check exit conditions once, paper-only (no broadcast)."""
    pos = load_position()
    if not pos:
        print("ABORT: no position state found. Run `init` first.")
        return 1
    price = get_price(pos.mint)
    if price is None:
        print("ABORT: no live price from Jupiter")
        return 1
    should, reason = check_exit(pos, price)
    pos.peak_price_usd = max(pos.peak_price_usd, price)
    save_position(pos)
    print(f"Price: ${price:.6f}  |  {reason}")
    if should:
        print("=> SELL TRIGGERED (paper only; run `sell <confirm>` to broadcast)")
    return 0


def cmd_sell(confirm: str, keypath: Optional[str] = None) -> int:
    pos = load_position()
    if not pos:
        print("ABORT: no position state found. Run `init` first.")
        return 1
    if pos.sell_executed:
        print(f"Already sold: {pos.mint}")
        return 0
    price = get_price(pos.mint)
    if price is None:
        print("ABORT: no live price from Jupiter")
        return 1
    est_usd = pos.token_amount * price
    print(f"Selling {pos.token_amount:,.4f} {pos.symbol} "
          f"(~${est_usd:.2f} @ ${price:.6f})")
    out = execute_sell(pos, confirm, keypath=keypath)
    if out.get("broadcast"):
        sig = out["receipt"].get("signature")
        print(f"BROADCAST: signature {sig}")
        print(f"verify: https://solscan.io/tx/{sig}")
    else:
        print(f"NO BROADCAST: {out.get('reason')}")
    return 0 if out.get("broadcast") else 1


def cmd_monitor(interval: float = 60.0, auto_sell: bool = False,
                confirm: Optional[str] = None,
                keypath: Optional[str] = None) -> int:
    """Loop: poll price, check exit, optionally auto-sell when triggered."""
    pos = load_position()
    if not pos:
        print("ABORT: no position state found. Run `init` first.")
        return 1
    if auto_sell and not confirm:
        print("ABORT: auto-sell requires --confirm")
        return 1
    print(f"Monitoring {pos.symbol} ({pos.mint[:14]}...) "
          f"every {interval:.0f}s. Entry ${pos.entry_price_usd:.6f}. "
          f"Auto-sell: {'ON' if auto_sell else 'OFF'}")
    try:
        while True:
            price = get_price(pos.mint)
            if price is None:
                print("  price fetch failed, retrying...")
                time.sleep(interval)
                continue
            should, reason = check_exit(pos, price)
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] ${price:.6f} | {reason}")
            if should and auto_sell and confirm:
                print("=> AUTO-SELL TRIGGERED")
                out = execute_sell(pos, confirm, keypath=keypath)
                if out.get("broadcast"):
                    sig = out["receipt"].get("signature")
                    print(f"BROADCAST: signature {sig}")
                    print(f"verify: https://solscan.io/tx/{sig}")
                    return 0
                else:
                    print(f"auto-sell failed: {out.get('reason')}")
                    return 1
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nMonitoring stopped by operator.")
        return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Memecoin exit engine (stop-loss, ratchet, sell)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="initialize position from on-chain balance")
    p_init.add_argument("mint", help="token mint address")
    p_init.add_argument("entry_sol", type=float, help="SOL spent on entry")
    p_init.add_argument("--owner", default=None, help="wallet address (default: derive from keypath)")

    p_check = sub.add_parser("check", help="check exit conditions once (paper)")

    p_sell = sub.add_parser("sell", help="execute live full-position sell")
    p_sell.add_argument("confirm", help="operator confirm string")
    p_sell.add_argument("--keypath", default=None, help="keypair JSON path")

    p_mon = sub.add_parser("monitor", help="poll price and auto-sell on trigger")
    p_mon.add_argument("--interval", type=float, default=60.0, help="poll seconds")
    p_mon.add_argument("--auto-sell", action="store_true", help="broadcast sell when triggered")
    p_mon.add_argument("--confirm", default=None, help="confirm string (required with --auto-sell)")
    p_mon.add_argument("--keypath", default=None, help="keypair JSON path")

    args = ap.parse_args(argv)
    keypath = resolve_keypath(getattr(args, "keypath", None))

    if args.cmd == "init":
        return cmd_init(args.mint, args.entry_sol, owner=args.owner)
    if args.cmd == "check":
        return cmd_check()
    if args.cmd == "sell":
        return cmd_sell(args.confirm, keypath)
    if args.cmd == "monitor":
        return cmd_monitor(args.interval, args.auto_sell, args.confirm, keypath)
    return 1


if __name__ == "__main__":
    sys.exit(main())
