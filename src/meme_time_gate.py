"""Time gate for live entries — G1 time-pattern analysis (2026-11-01).

Wraps the live trader's entry logic with UTC windows derived from full
G1 blotter analysis (127 entries / 8.65 days, 2026-10-23 -> 2026-11-01):

GOLD windows (positive net PnL per entry-hour analysis):
  - 04:00–07:00 UTC  (+$25.6 net, 44% win, n=13)
  - 10:00–12:00 UTC  (+$148.8 net, 43% win, n=10)
  - 18:00–20:00 UTC  (+$93.8 net, 50% win, n=11)

DEATH zones (0% win rate or consistently negative expectancy):
  - 00:00–02:00 UTC  (0% win, -$73.6 net, n=7)
  - 20:00–00:00 UTC  (negative net, n=25)

NEUTRAL: everything else (screen but no strong signal).
Hours 00:00 and 01:00 both recorded 0% win rate; 20:00–23:00 were
consistently net-negative. Earlier partial analysis (102 round-trips)
claimed 17:00/19:00 as best hours, but full-blotter review shows 19:00
at -$3.83 net (3 rugs, n=6) — partial analysis was unreliable.

Time source: fetches real UTC from an external HTTP server's Date header,
with local-clock fallback. The local Windows clock can drift or run in the
wrong timezone, so we never trust it blindly.

Usage:
    from src.meme_time_gate import is_entry_window
    if is_entry_window():
        # proceed with entry
    else:
        # skip — outside gold windows
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Optional
import urllib.request
import urllib.error


# Entry windows as (start_hour, end_hour) inclusive of start, exclusive of end.
GOLD_WINDOWS = [
    (4, 7),    # 04:00–07:00 UTC
    (10, 12),  # 10:00–12:00 UTC
    (18, 20),  # 18:00–20:00 UTC
]

DEATH_WINDOWS = [
    (0, 2),    # 00:00–02:00 UTC (0% win rate)
    (20, 24),  # 20:00–00:00 UTC (consistently negative)
]


def hour_in_windows(hour: int, windows: list[tuple[int, int]]) -> bool:
    """True if `hour` falls inside any (start, end) window."""
    return any(start <= hour < end for start, end in windows)


class _TimeSource:
    """Fetches real UTC from an external HTTP Date header, caches with TTL.

    Never trusts the local system clock blindly — Windows can run in the
    wrong timezone or drift. Falls back to local clock + cached offset
    when the network is unreachable.
    """

    def __init__(self) -> None:
        self._offset_seconds: Optional[float] = None  # external_utc - local_utc
        self._last_fetch: float = 0.0
        self._ttl_seconds: float = 300.0  # refresh offset every 5 min

    def _fetch_external_utc(self) -> Optional[float]:
        """Try to read UTC from an HTTP server's Date header."""
        for url in (
            "https://api.github.com",
            "https://httpbin.org/get",
            "https://api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112",
        ):
            try:
                req = urllib.request.Request(
                    url,
                    method="GET",
                    headers={"User-Agent": "TARS-time-gate/1.0"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    date_str = resp.headers.get("Date")
                    if date_str:
                        # Parse "Wed, 21 Oct 2015 07:28:00 GMT"
                        dt = datetime.strptime(
                            date_str, "%a, %d %b %Y %H:%M:%S GMT"
                        ).replace(tzinfo=timezone.utc)
                        return dt.timestamp()
            except (urllib.error.URLError, OSError, ValueError, Exception):
                continue
        return None

    def now(self) -> float:
        """Return current UTC timestamp from external source or local fallback."""
        now_local = time.time()

        # Refresh external offset if TTL expired
        if (
            self._offset_seconds is None
            or now_local - self._last_fetch > self._ttl_seconds
        ):
            external = self._fetch_external_utc()
            if external is not None:
                self._offset_seconds = external - now_local
                self._last_fetch = now_local

        if self._offset_seconds is not None:
            return now_local + self._offset_seconds
        return now_local  # last resort: trust local clock


_time_source = _TimeSource()


def now_utc() -> float:
    """Current UTC timestamp from external source with local fallback."""
    return _time_source.now()


def is_entry_window(ts: float | None = None) -> tuple[bool, str]:
    """Return (allowed, reason) for whether NOW is a good entry window.

    Uses UTC hour from external time source. Gold windows allowed; death
    windows hard-refuse; everything else is neutral (allowed but
    unremarkable).
    """
    if ts is None:
        ts = now_utc()
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour

    if hour_in_windows(hour, DEATH_WINDOWS):
        return False, f"death window: UTC {hour}:00 (high rug rate)"
    if hour_in_windows(hour, GOLD_WINDOWS):
        return True, f"gold window: UTC {hour}:00 (low rug rate)"
    return True, f"neutral window: UTC {hour}:00 (no strong signal)"


def in_gold_window(ts: float | None = None) -> bool:
    """Convenience: True only inside gold windows."""
    if ts is None:
        ts = now_utc()
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    return hour_in_windows(hour, GOLD_WINDOWS)


def clock_offset() -> Optional[float]:
    """Return cached external-vs-local clock offset in seconds, or None."""
    return _time_source._offset_seconds
