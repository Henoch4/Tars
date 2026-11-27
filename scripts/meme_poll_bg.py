"""Background poller — post-G1-failure path.

Continuous time-gated screening loop for the Book-2 satellite after G1
failed. Skips death windows, screens during gold/neutral windows, reports
a status table each cycle, and stops+reports the moment a candidate
clears all gates. Nothing broadcasts automatically — the operator says
"yes" with a confirm string before any live trade.

Usage:
    python scripts/meme_poll_bg.py                 # foreground run
    python scripts/meme_poll_bg.py --daemon        # background (nohup + shell)
    python scripts/meme_poll_bg.py --status        # one-shot status table
    python scripts/meme_poll_bg.py --screen        # one screening pass

The poller is deliberately read-only until the operator confirms:
- It screens candidates (paper verdicts only)
- It never calls live_buy/live_sell with a confirm string
- On a pass, it stops and prints what the operator must say "yes" to

Time gate (from src/meme_time_gate.py, derived from G1 window-4 analysis):
  GOLD windows (low rug rate):   02:00–07:00 UTC, 16:00–19:00 UTC
  DEATH windows (skip screens):  00:00–02:00, 07:00–11:00, 12:00–13:00,
                                 20:00–00:00 UTC
  Neutral: everything else (screen but no strong signal)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The status table uses en/em dashes; Windows consoles default to cp1252 and
# would mojibake them. Force UTF-8 where the stream supports it.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.meme_time_gate import is_entry_window, GOLD_WINDOWS, DEATH_WINDOWS
from src.meme_live_trader import screen_cycle, load_position

AGENT_ENV = "/etc/tars/agent.env"
KEYPATH_DEFAULT = "/etc/tars/book2-satellite.json"

INTERVAL_S = 60.0
MAX_CYCLES = 144  # 24 hours at 60s
SOL_LAMPORTS = 1_000_000_000


# ---------------------------------------------------------------------------
# Wallet / balance
# ---------------------------------------------------------------------------

def _load_agent_env() -> None:
    """Load SOLAMI_*/RPC_FAST_* from agent.env into os.environ."""
    for path in (AGENT_ENV,):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    if key and key not in os.environ:
                        os.environ[key] = val.strip().strip('"').strip("'")
        except OSError:
            pass


def _solana_rpc_url() -> Optional[str]:
    """Resolve a Solana JSON-RPC URL from env (Solami preferred, RPC Fast
    fallback). Returns None if neither is configured."""
    load_agent_env_if_needed()
    solami = (os.environ.get("SOLAMI_RPC_URL") or "").strip() or \
        (os.environ.get("SOLAMI_API_KEY") and
         f"https://rpc.solami.fast/sol?api_key={os.environ['SOLAMI_API_KEY'].strip()}")
    if solami:
        return solami
    rpc = (os.environ.get("RPC_FAST_URL") or "").strip()
    if rpc:
        return rpc
    return None


_load_agent_env_called = False


def load_agent_env_if_needed() -> None:
    global _load_agent_env_called
    if not _load_agent_env_called:
        _load_agent_env()
        _load_agent_env_called = True


def getbalance_request(pubkey: str, url: str) -> "urllib.request.Request":
    """Build the Solana getBalance POST for one pubkey. Pure — no I/O here;
    the network call site stays separated so this is unit-testable."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getBalance",
        "params": [pubkey, {"commitment": "confirmed"}],
    }
    return urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (TARS poller)"},
        method="POST",
    )


def wallet_balance_sol(keypath: str = KEYPATH_DEFAULT) -> tuple[Optional[str], Optional[float]]:
    """Return (pubkey_prefix, balance_sol) for the Book-2 wallet.

    Reads the keypair file for the pubkey, then queries getBalance via the
    configured Solana RPC. Returns (None, None) if anything is missing or
    fails — never fabricates a balance."""
    try:
        from solders.pubkey import Pubkey
        from solders.keypair import Keypair
    except Exception:
        return None, None
    try:
        secret = bytes(json.loads(open(keypath, encoding="utf-8").read()))
        if len(secret) != 64:
            return None, None
        kp = Keypair.from_bytes(secret)
        pubkey = str(kp.pubkey())
    except Exception:
        return None, None

    url = _solana_rpc_url()
    if not url:
        return pubkey, None

    req = getbalance_request(pubkey, url)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            out = json.loads(resp.read().decode())
    except Exception:
        return pubkey, None

    try:
        lamports = int(out["result"]["value"])
        return pubkey, lamports / SOL_LAMPORTS
    except (KeyError, TypeError, ValueError):
        return pubkey, None


def open_positions_count() -> int:
    """Count OPEN live positions from the live positions file.

    A position whose `sell_executed` is set is already closed — it must not
    be reported as open (a stale closed row used to read as 1 open).
    """
    try:
        pos = load_position()
    except Exception:
        return 0
    if pos is None:
        return 0
    if getattr(pos, "sell_executed", False):
        return 0
    if getattr(pos, "token_amount", 0) <= 0:
        return 0
    return 1


# ---------------------------------------------------------------------------
# Time gate helpers
# ---------------------------------------------------------------------------

def _next_gold_window(now: datetime | None = None) -> tuple[str, float]:
    """Return (label, seconds_until) for the next gold window start."""
    if now is None:
        now = datetime.now(timezone.utc)
    today = now.date()
    best: Optional[tuple[str, float]] = None
    for start_h, end_h in GOLD_WINDOWS:
        start = datetime(today.year, today.month, today.day, start_h,
                         tzinfo=timezone.utc)
        if start <= now:
            start += timedelta(days=1)
        secs = (start - now).total_seconds()
        if best is None or secs < best[1]:
            best = (f"{start_h:02d}:00–{end_h:02d}:00 UTC", secs)
    if best is None:
        return "unknown", float("inf")
    return best


def _window_status(now: datetime | None = None) -> str:
    """Human-readable: which window we're in and what we're doing."""
    if now is None:
        now = datetime.now(timezone.utc)
    hour = now.hour
    for start, end in DEATH_WINDOWS:
        if start <= hour < end:
            return f"death window — skipping screens"
    for start, end in GOLD_WINDOWS:
        if start <= hour < end:
            return f"gold window — screening"
    return "neutral window — screening (no strong signal)"


# ---------------------------------------------------------------------------
# Status table
# ---------------------------------------------------------------------------

def print_status_table(shell_id: str, cycle: int, max_cycles: int,
                       interval_s: float, keypath: str = KEYPATH_DEFAULT,
                       extra: Optional[str] = None) -> None:
    """Print the poller status table (matches the operator-readable format)."""
    now = datetime.now(timezone.utc)
    allowed, reason = is_entry_window()
    pubkey, bal = wallet_balance_sol(keypath)
    pub = f"{pubkey[:8]}…{pubkey[-4:]}" if pubkey else "unavailable"
    bal_s = f"{bal:.3f} SOL" if bal is not None else "n/a"
    pos_n = open_positions_count()
    win_label, win_secs = _next_gold_window(now)
    mins = int(win_secs // 60)

    print("Poller running in the background.")
    print()
    print("| Setting | Value |")
    print("|---|---|")
    print(f"| **Poller** | Running (shell `{shell_id}`) |")
    print(f"| **Interval** | Every {interval_s:.0f} seconds |")
    print(f"| **Cycle** | {cycle} / {max_cycles} |")
    print(f"| **Time gate** | Active — {now.strftime('%H:%M')} UTC, "
          f"{_window_status(now)} |")
    print(f"| **Next gold window** | {win_label} (starts in ~{mins} min) |")
    print(f"| **Wallet** | `{pub}` — {bal_s}, {pos_n} open position(s) |")
    if extra:
        print(f"| **Note** | {extra} |")
    print()
    print("It will skip death windows, retry on network errors, and "
          "stop+report the moment a candidate clears all gates. "
          "Nothing trades automatically — you say \"yes\" with your "
          "confirm string before anything broadcasts.")


# ---------------------------------------------------------------------------
# Screening with retry
# ---------------------------------------------------------------------------

def screen_with_retry(limit: int = 10, max_retries: int = 5,
                      backoff_cap_s: float = 30.0) -> list:
    """Run one screening cycle with exponential-backoff retries on
    transient network errors."""
    for attempt in range(1, max_retries + 1):
        try:
            return screen_cycle(limit=limit)
        except Exception as e:
            wait = min(2 ** attempt, backoff_cap_s)
            print(f"  screen attempt {attempt} failed: "
                  f"{type(e).__name__} {e} — retrying in {wait:.0f}s")
            time.sleep(wait)
    print(f"  screen failed after {max_retries} retries, skipping cycle")
    return []


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def poll_loop(interval_s: float = INTERVAL_S, max_cycles: int = MAX_CYCLES,
              limit: int = 10, keypath: str = KEYPATH_DEFAULT,
              daemon: bool = False) -> int:
    """Run the poller until a candidate passes or max_cycles is reached."""
    shell_id = os.environ.get("SHELL_ID") or \
        f"sh_{os.getpid():x}{int(time.time())%100000:x}"
    cycle = 0

    print_status_table(shell_id, cycle, max_cycles, interval_s, keypath,
                       extra="starting poller")

    while cycle < max_cycles:
        cycle += 1
        now = datetime.now(timezone.utc)
        allowed, reason = is_entry_window()

        print(f"\n=== poll cycle {cycle}/{max_cycles} | "
              f"{now.strftime('%H:%M:%S')} UTC ===")
        print(f"  time gate: {reason}")

        if not allowed:
            print(f"  death window — skipping screen, next check in "
                  f"{interval_s:.0f}s")
            time.sleep(interval_s)
            continue

        passed = screen_with_retry(limit=limit)

        if passed:
            print(f"\n>>> PASS FOUND after {cycle} cycle(s)")
            for snap in passed:
                sym = (snap.symbol or "").encode("ascii", "replace").decode("ascii")
                print(f"    MINT={snap.mint}")
                print(f"    symbol={sym}")
                print(f"    liquidity=${(snap.liquidity_usd or 0):,.0f}")
                print(f"    mcap=${(snap.mcap_usd or 0):,.0f}")
                print(f"    round_trip={snap.round_trip_cost_pct:.2f}%")
                print(f"    rugcheck={snap.rugcheck_rugged}")
                print(f"    creator_hold={snap.creator_hold_pct}%")
                print(f"    top10={snap.top10_hold_pct}%")
                print(f"    age={getattr(snap, 'age_s', 0)/3600:.1f}h")
            print("\n>>> STOPPED — candidate cleared all gates.")
            print(">>> To trade: say 'yes' with the mint + size ($8 default) "
                  "+ confirm string.")
            print(">>> Nothing broadcasts until you confirm.")
            return 0

        if cycle < max_cycles:
            print(f"  Nothing passed. Next screen in {interval_s:.0f}s...")
            time.sleep(interval_s)

    print(f"\nNo pass after {max_cycles} cycles (~{max_cycles*interval_s/3600:.0f}h). "
          "Stopping.")
    return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Background poller — time-gated screening, post-G1-failure path")
    ap.add_argument("--daemon", action="store_true",
                    help="run in background via nohup (prints shell id)")
    ap.add_argument("--status", action="store_true",
                    help="print one-shot status table and exit")
    ap.add_argument("--screen", action="store_true",
                    help="run one screening pass and exit")
    ap.add_argument("--interval", type=float, default=INTERVAL_S,
                    help="seconds between cycles")
    ap.add_argument("--max-cycles", type=int, default=MAX_CYCLES,
                    help="max cycles before stopping")
    ap.add_argument("--limit", type=int, default=10,
                    help="max candidates per screen")
    ap.add_argument("--keypath", default=KEYPATH_DEFAULT,
                    help="Book-2 keypair path (wallet balance)")
    args = ap.parse_args(argv)

    # Enrichment HeliusFeed/Solami read HELIUS_*/SOLAMI_* from the process
    # env; on the VPS they live in /etc/tars/agent.env and there is no
    # repo .env, so load_dotenv() alone leaves them empty and every screen
    # fails-closed ("enrichment failed" => 0 candidates). Inject env here,
    # BEFORE any screening.
    load_agent_env_if_needed()

    if args.status:
        shell_id = os.environ.get("SHELL_ID") or f"sh_{os.getpid():x}"
        print_status_table(shell_id, 0, args.max_cycles, args.interval,
                           args.keypath, extra="one-shot status")
        return 0

    if args.screen:
        passed = screen_with_retry(limit=args.limit)
        print(f"{len(passed)} passed all gates")
        for snap in passed:
            print(f"  {snap.mint[:14]}...  liq=${(snap.liquidity_usd or 0):,.0f}  "
                  f"rt={snap.round_trip_cost_pct}%  rug={snap.rugcheck_rugged}")
        return 0

    if args.daemon:
        # Re-exec under nohup so it survives shell exit.
        import subprocess
        cmd = [sys.executable, os.path.abspath(__file__),
               "--interval", str(args.interval),
               "--max-cycles", str(args.max_cycles),
               "--limit", str(args.limit),
               "--keypath", args.keypath]
        log = "meme_poll_bg.log"
        with open(log, "a") as lf:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        shell_id = f"sh_{proc.pid:x}{int(time.time())%100000:x}"
        print_status_table(shell_id, 0, args.max_cycles, args.interval,
                           args.keypath,
                           extra=f"daemon PID={proc.pid}, log={log}")
        return 0

    return poll_loop(interval_s=args.interval, max_cycles=args.max_cycles,
                     limit=args.limit, keypath=args.keypath)


if __name__ == "__main__":
    sys.exit(main())
