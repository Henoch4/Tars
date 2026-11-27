"""Poll until a candidate passes all gates, then stop and report.
Separate from G1 — writes only to live_blotter.jsonl / live_breakers.json.

Respects the time gate: skips screening during death windows (20:00-00:00,
00:00-02:00, 07:00-11:00, 12:00-13:00 UTC), only screens during gold
(02:00-07:00, 16:00-19:00) and neutral windows.

Retries transient network errors (DNS, timeouts, 5xx) with exponential
backoff so a hiccup doesn't end the run.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.meme_live_trader import screen_cycle, LIVE_BLOTTER
from src.meme_time_gate import is_entry_window


def poll_until_pass(interval_s: float = 60.0, limit: int = 10,
                    max_cycles: int = 0, max_retries: int = 5):
    """Run screen cycles until one passes, then stop."""
    cycle = 0
    while True:
        cycle += 1
        now = time.time()
        allowed, reason = is_entry_window(now)

        print(f"\n=== poll cycle {cycle} | {time.strftime('%H:%M:%S')} UTC ===")
        print(f"  time gate: {reason}")

        if not allowed:
            print(f"  death window — skipping screen, next check in {interval_s:.0f}s")
            time.sleep(interval_s)
            continue

        # Retry wrapper around screen_cycle (network flakiness -> retry)
        passed = None
        for attempt in range(1, max_retries + 1):
            try:
                passed = screen_cycle(limit=limit)
                break
            except Exception as e:
                wait = min(2 ** attempt, 30)
                print(f"  screen attempt {attempt} failed: {type(e).__name__} "
                      f"{e} — retrying in {wait}s")
                time.sleep(wait)
        if passed is None:
            print(f"  screen failed after {max_retries} retries, skipping cycle")
            time.sleep(interval_s)
            continue

        if passed:
            print(f"\n>>> PASS FOUND after {cycle} cycle(s)")
            for snap in passed:
                sym = (snap.symbol or "").encode("ascii", "replace").decode("ascii")
                print(f"    MINT={snap.mint}")
                print(f"    symbol={sym}")
                print(f"    liquidity=${snap.liquidity_usd:,.0f}")
                print(f"    mcap=${snap.mcap_usd:,.0f}")
                print(f"    round_trip={snap.round_trip_cost_pct:.2f}%")
                print(f"    rugcheck={snap.rugcheck_rugged}")
                print(f"    creator_hold={snap.creator_hold_pct}%")
                print(f"    top10={snap.top10_hold_pct}%")
                print(f"    age={snap.age_s/3600:.1f}h")
            print("\n>>> To trade: say 'yes' with the mint + size ($8 default) + confirm string")
            return passed
        if max_cycles and cycle >= max_cycles:
            print(f"\nNo pass after {max_cycles} cycles. Stopping.")
            return []
        print(f"Nothing passed. Next screen in {interval_s:.0f}s...")
        time.sleep(interval_s)


if __name__ == "__main__":
    poll_until_pass()
