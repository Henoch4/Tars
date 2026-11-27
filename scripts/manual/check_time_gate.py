"""Manual UTC-window diagnostic; importing this module makes no network calls."""
import argparse
from datetime import datetime, timezone

from src.meme_time_gate import is_entry_window, in_gold_window, now_utc, clock_offset


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(f"External UTC now: {datetime.fromtimestamp(now_utc(), tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Clock offset (external - local): {clock_offset()} seconds")

    allowed, reason = is_entry_window()
    print(f"Time gate: {'ALLOWED' if allowed else 'REFUSED'} — {reason}")
    print(f"In gold window: {in_gold_window()}")

    # Test specific hours
    test_hours = [0, 1, 2, 3, 4, 7, 11, 12, 13, 16, 17, 19, 20, 22, 23]
    for h in test_hours:
        dt = datetime(2026, 10, 29, h, 0, 0, tzinfo=timezone.utc)
        allowed, reason = is_entry_window(dt.timestamp())
        gold = 'GOLD' if in_gold_window(dt.timestamp()) else '      '
        print(f'  {h:02d}:00 UTC: allowed={str(allowed):5s} | {gold} | {reason}')


if __name__ == "__main__":
    main()
