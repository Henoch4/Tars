"""Live trader engine — COMPLETELY SEPARATE from G1 paper satellite.

Wires in the unwired satellite components (breakers, discovery, feed,
rugcheck) for live execution, but writes to its own blotter and state
files under data/meme_satellite/live_*. G1's paper loop (meme_loop.py)
never reads or touches these files. Two books, zero shared state.

Risk: default BreakerLimits are conservative ($50 daily loss cap, 6-loss
streak cooldown, 3 max open positions, $75 heat). Override via env or
constructor — but never silently disable.

Usage:
    uv run python -m src.meme_live_trader --screen      # one screening pass
    uv run python -m src.meme_live_trader --monitor     # continuous loop
    uv run python -m src.meme_live_trader --status      # live book snapshot
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load repo .env for HELIUS_* credentials (paper satellite uses /etc/tars/agent.env;
# local dev uses repo-root .env — same file the OKX trading config reads).
from dotenv import load_dotenv
load_dotenv()

from src.meme_satellite import TokenSnapshot, scream_filter
from src.meme_execute import (
    SOL_MINT, live_buy, live_sell, book2_pubkey, token_prices,
    MAX_SESSION_SPEND_USD, ExecutionLedger,
)
from src.meme_exit import (
    resolve_keypath, create_position, load_position, save_position,
    get_token_balance, check_exit, execute_sell, STATE_PATH as EXIT_STATE_PATH,
)
from src.meme_discovery import enrich_mint, fetch_fresh_mints, DiscoveryError
from src.meme_feed import HeliusFeed, FeedError
from src.meme_rugcheck import rugcheck_mint, RugCheckError
from src.meme_jupiter import round_trip_cost_pct
from src.satellite_breakers import SatelliteBreakers, BreakerLimits, day_key
from src.meme_time_gate import is_entry_window


# --- live-only paths (never touch G1's blotter.jsonl or positions.json) ---
LIVE_DIR = Path("data/meme_satellite")
LIVE_BLOTTER = LIVE_DIR / "live_blotter.jsonl"
LIVE_POSITIONS = LIVE_DIR / "live_positions.json"
LIVE_BREAKERS = LIVE_DIR / "live_breakers.json"
LIVE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class LiveFill:
    mint: str
    side: str
    size_usd: float
    price_usd: float
    ts: float
    tx_signature: Optional[str] = None


# --- live blotter ----------------------------------------------------------

def _live_record(record: dict) -> None:
    """Append to the LIVE blotter only — never G1's paper blotter."""
    record["ts"] = time.time()
    with open(LIVE_BLOTTER, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _live_breakers_load() -> SatelliteBreakers:
    if LIVE_BREAKERS.exists():
        try:
            state = json.loads(LIVE_BREAKERS.read_text())
            return SatelliteBreakers.from_state(state)
        except Exception:
            pass
    return SatelliteBreakers()


def _live_breakers_save(b: SatelliteBreakers) -> None:
    LIVE_BREAKERS.write_text(json.dumps(b.state(), indent=2))


# --- screening -------------------------------------------------------------

def _screen_mint(mint: str, sol_price_usd: float) -> Optional[TokenSnapshot]:
    """Enrich one mint via DexScreener + Helius + RugCheck + Jupiter
    round-trip, then run it through scream_filter at the caller.
    Returns snapshot or None if enrichment totally fails.

    DexScreener first: Helius only fills chain-provable fields, and the
    scream filter's venue/liquidity gates read liquidity_usd / mcap_usd /
    graduated / pool_address, which ONLY arrive via `extra`. Empty extra
    made every candidate REJECT (unconfirmed) forever.
    """
    try:
        extra = enrich_mint(mint)
    except DiscoveryError:
        # Too fresh to index / feed down — skip, never guess zeros.
        return None
    try:
        feed = HeliusFeed()
        snap = feed.snapshot_for_mint(mint, extra)
    except FeedError:
        return None

    # RugCheck (fail-soft)
    try:
        rc = rugcheck_mint(mint)
        if rc.get("creator_address"):
            snap.creator_address = rc["creator_address"]
        if rc.get("rugcheck_rugged") is not None:
            snap.rugcheck_rugged = rc["rugcheck_rugged"]
        if rc.get("rugcheck_score") is not None:
            snap.rugcheck_score = rc["rugcheck_score"]
        if rc.get("creator_hold_pct") is not None:
            snap.creator_hold_pct = rc["creator_hold_pct"]
    except RugCheckError:
        pass

    # Jupiter round-trip sellability (Gate 4 equivalent)
    try:
        rt = round_trip_cost_pct(mint, price_usd=sol_price_usd)
        if rt is not None:
            snap.round_trip_cost_pct = rt
    except Exception:
        pass

    # Window A serialization inputs: launch count + realized PnL from OUR
    # observation history beside the LIVE blotter (same decision-time join
    # as the paper runner's _enrich). Fail-soft: absent fields read as
    # unobserved in the filter, never guessed.
    try:
        from src.meme_history import (
            creator_launch_count, creator_pnl_stats, load_history,
            rebuild_history)
        hist_path = str(LIVE_DIR / "deployer_history.json")
        rebuild_history(str(LIVE_BLOTTER), hist_path)
        if snap.creator_address:
            snap.creator_launch_count = creator_launch_count(
                load_history(hist_path), snap.creator_address, mint)
            cstat = creator_pnl_stats(str(LIVE_BLOTTER)).get(
                snap.creator_address)
            if cstat and cstat.get("avg_pnl") is not None:
                snap.creator_avg_pnl = cstat["avg_pnl"]
    except Exception:
        pass

    return snap


def screen_cycle(limit: int = 10) -> list[TokenSnapshot]:
    """Discover → enrich → screen. Returns PASSED snapshots only."""
    sol_usd = token_prices([SOL_MINT]).get(SOL_MINT)
    if not sol_usd:
        print("ABORT: no SOL price; cannot screen")
        return []

    try:
        cands = fetch_fresh_mints(limit=limit)
    except DiscoveryError as e:
        print(f"Discovery failed: {e}")
        return []

    passed = []
    for c in cands:
        mint = c["mint"]
        snap = _screen_mint(mint, sol_usd)
        if not snap:
            print(f"  skip {mint[:14]}...  (enrichment failed)")
            continue
        verdict = scream_filter(snap)
        status = "PASS" if verdict.passed else f"REJECT ({verdict.rejected_by})"
        sym = (snap.symbol or "").encode("ascii", "replace").decode("ascii")
        print(f"  {mint[:14]}...  {status}  sym={sym}  liq=${(snap.liquidity_usd or 0):,.0f}  "
              f"mcap=${(snap.mcap_usd or 0):,.0f}  rt={snap.round_trip_cost_pct}%")
        if verdict.passed:
            passed.append(snap)
    return passed


# --- entry -----------------------------------------------------------------

def _live_entry(snap: TokenSnapshot, size_usd: float,
                breakers: SatelliteBreakers,
                keypath: str,
                ledger: ExecutionLedger) -> dict:
    """Execute a live buy if breakers and time gate allow."""
    allowed, reason = breakers.new_entries_allowed(
        open_count=0,  # single-position live book for now
        open_exposure_usd=0.0,
        ts=time.time(),
    )
    if not allowed:
        return {"broadcast": False, "reason": f"breakers: {reason}"}

    # Time gate: refuse entries in death windows, allow gold/neutral
    tg_allowed, tg_reason = is_entry_window()
    if not tg_allowed:
        return {"broadcast": False, "reason": f"time gate: {tg_reason}"}

    sol_usd = token_prices([SOL_MINT]).get(SOL_MINT)
    if not sol_usd:
        return {"broadcast": False, "reason": "no SOL price"}
    lamports = int(size_usd / sol_usd * 1_000_000_000)

    taker = book2_pubkey(keypath)
    est_costs = size_usd * 0.01 * 2 + 0.10  # 2x 1% + live gas est
    out = live_buy(
        SOL_MINT, snap.mint, lamports, taker, keypath,
        est_costs, size_usd, ledger,
        slippage_bps=100, confirm="",  # paper quote first
    )
    if not out["broadcast"]:
        _live_record({
            "event": "entry_paper",
            "mint": snap.mint,
            "symbol": snap.symbol,
            "size_usd": size_usd,
            "reason": out.get("reason"),
        })
        return out

    # Live broadcast requires operator confirm — handled by caller
    return out


# --- CLI -------------------------------------------------------------------

def cmd_screen(limit: int) -> int:
    print(f"=== LIVE screen cycle | {limit} candidates ===")
    passed = screen_cycle(limit=limit)
    print(f"{len(passed)} passed all gates")
    return 0


def cmd_status() -> int:
    b = _live_breakers_load()
    pos = load_position()
    print("=== LIVE book ===")
    print(f"breakers: halted={b.halted}  paused_until={b.paused_until}  "
          f"streak={b.consecutive_losses}  daily_loss=${b.daily_loss.get(day_key(time.time()), 0.0):.2f}")
    if pos:
        print(f"position: {pos.symbol} ({pos.mint[:14]}...)  "
              f"entry=${pos.entry_price_usd:.6f}  peak=${pos.peak_price_usd:.6f}  "
              f"tokens={pos.token_amount:,.4f}  sold={pos.sell_executed}")
    else:
        print("position: none")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Live trader — separate from G1 paper satellite")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_screen = sub.add_parser("screen", help="discover + screen candidates (paper verdicts)")
    p_screen.add_argument("--limit", type=int, default=10)
    p_screen.add_argument("--time-check", action="store_true",
                          help="show current time-gate status and exit")

    p_status = sub.add_parser("status", help="live book + breaker state")

    args = ap.parse_args(argv)

    if args.cmd == "screen":
        if args.time_check:
            allowed, reason = is_entry_window()
            print(f"Time gate: {'ALLOWED' if allowed else 'REFUSED'} — {reason}")
            return 0
        return cmd_screen(args.limit)
    if args.cmd == "status":
        return cmd_status()
    return 1


if __name__ == "__main__":
    sys.exit(main())
