#!/usr/bin/env python3
"""Fetch the research dataset for the funding-path / carry-timing model
(ML_ROADMAP_REVISED.md, 2026-08-23 target #1 — workplan steps 1-2, extended
to the tier-2 dataset the same day: ~60 symbols, delisted funding, premium
index).

Binance-only on purpose: one API family, full funding depth, plain-named
perps (no 1000X spot/perp name mismatches). OKX stays the trading venue;
the OKX->Binance proxy question is tracked (venue overlap check in
run_validation_gate.py, and the daily archiver below is how the OKX-native
series gets built over time).

Writes to data/carry/ (gitignored, regenerable — safe to re-run, cached
per file, --refresh forces refetch):
  {SYM}_funding.csv    ts_ms, rate                     (8h realizations, 4y)
  {SYM}_perp_1h.csv    ts_ms, open, high, low, close, vol (USDT-perp klines, 2y)
  {SYM}_spot_1h.csv    ts_ms, open, high, low, close, vol (spot klines, 2y)
  {SYM}_premium.csv    ts_ms, premium_open, premium_close (premium index, 5m, 2y;
                                                       live tier-1 symbols only —
                                                       the direct causal funding input)
  delisted/{SYM}_funding.csv                           (funding ONLY — candles
                                                       for dead perps are gone;
                                                       the 2022 collapse cohort
                                                       is the tail-regime data
                                                       the survivors' files
                                                       cannot contain)

Daily archiver: a minimal daily run (e.g. via cron) fetches ~1 day of
funding + premium per symbol. Over weeks this builds the full 4y history.
The script is idempotent — each run appends only new bars, and existing
files are never corrupted. Set CRON=1 in the environment to enable the
one-shot daily-mode (no interactive prompt, just fetch what's new).

Failures skip per symbol and are reported; a run is always safe to re-run.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "carry"

# Liquid plain-named USDT perps (research pooling — the live asset list
# stays BTC/ETH/SOL/BNB per EXPANSION_ROADMAP.md). Missing/short symbols
# are skipped gracefully. Tier-1 (top 20 by market cap) + Tier-2 (remaining
# viable perps). Total ~60 symbols.
TIER1 = [
    "BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK",
    "DOT", "LTC", "BCH", "UNI", "ATOM", "NEAR", "APT", "ARB", "OP",
    "FIL", "INJ",
]
TIER2 = [
    "SEI", "SUI", "TRX", "ETC", "ICP", "IMX", "RENDER", "HBAR", "VET",
    "ALGO", "STX", "GRT", "LDO", "CRV", "MKR", "AAVE", "SNX", "COMP",
    "RUNE", "KAVA", "EGLD", "FLOW", "XTZ", "SAND", "MANA", "AXS", "GALA",
    "CHZ", "ENJ", "WLD", "JUP", "PYTH", "ORDI", "NOT", "STRK", "ZK",
    "BLUR", "SUSHI", "YFI", "DASH", "ENA", "GMX", "CAKE",
    "BAT", "ZRX", "ENS", "PEOPLE", "TIA",
]
SYMBOLS = TIER1 + TIER2

# The 2022 collapse cohort: delisted perps whose funding spirals are the
# tail regimes absent from every survivor file. Funding history may or may
# not still be served — the script reports what it gets. Candles are NOT
# fetched (gone with the listings); the dataset builder treats them as
# missing and NaN-fills candle features for these symbols.
DELISTED = [
    "LUNA",   # Terra classic collapse, May 2022
    "UST",    # Terra's algorithmic stablecoin death spiral
    "ANC",    # Anchor Protocol
    "MIR",    # Mirror Protocol
    "SRM",    # Serum / FTX collapse, Nov 2022
    "FTT",    # FTX token
    "BCC",    # Bitcoin Cash ABC delisting
]

# The 2022 collapse cohort: delisted perps whose funding spirals are the
# tail regimes absent from every survivor file. Funding history may or may
# not still be served — the script reports what it gets. Candles are NOT
# fetched (gone with the listings); the dataset builder treats them as
# missing and NaN-fills candle features for these symbols.
DELISTED = [
    "LUNA",   # Terra classic collapse, May 2022
    "UST",    # Terra's algorithmic stablecoin death spiral
    "ANC",    # Anchor Protocol
    "MIR",    # Mirror Protocol
    "SRM",    # Serum / FTX collapse, Nov 2022
    "FTT",    # FTX token
    "BCC",    # Bitcoin Cash ABC delisting
]

# Premium index: live tier-1 symbols only (the direct causal funding input).
# Tier-2+ delisted symbols don't have a Binance premium index.
PREMIUM_SYMBOLS = TIER1  # BTC, ETH, BNB, SOL, XRP, DOGE, ADA, AVAX, LINK, DOT, LTC, BCH, UNI, ATOM, NEAR, APT, ARB, OP, FIL, INJ

SPOT = "https://api.binance.com"
FAPI = "https://fapi.binance.com"
SLEEP = 0.12
UA = {"User-Agent": "AuditTrailTrader-carry-research/1.0"}


from urllib.error import HTTPError


def _get(base: str, path: str, params: dict[str, str]):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    for attempt in range(4):
        try:
            req = urllib.request.Request(f"{base}{path}?{qs}", headers=UA)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            # 4xx is a hard, non-transient error (404 missing route, 418/429 ban,
            # 451 geo-block). Retrying only adds ban/rate pressure — fail fast and
            # let the per-symbol handler report it. Retry 5xx (transient) instead.
            if e.code < 500:
                raise
            print(f"  retry {path} (5xx {e.code}): {e}")
            time.sleep(1.0 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            if attempt == 3:
                raise
            print(f"  retry {path}: {e}")
            time.sleep(1.0 * (attempt + 1))
    return []


def fetch_klines(base: str, path: str, symbol: str, years: float = 2.0,
                 limit: int = 1500, since_ms: int | None = None) -> list[list]:
    start = since_ms if since_ms is not None else int(
        (time.time() - years * 365 * 24 * 3600) * 1000)
    rows: list[list] = []
    while True:
        page = _get(base, path, {"symbol": symbol, "interval": "1h",
                                 "startTime": str(start), "limit": str(limit)})
        if not page:
            break
        rows.extend([r[:6] for r in page])
        last = int(page[-1][0])
        print(f"  {symbol} {path}: {len(rows)} bars "
              f"(to {time.strftime('%Y-%m-%d', time.gmtime(last / 1000))})", end="\r")
        if len(page) < limit or last >= (time.time() - 3600) * 1000:
            break
        start = last + 1
        time.sleep(SLEEP)
    print()
    rows.sort(key=lambda r: r[0])
    return rows


def fetch_funding(symbol: str, years: float = 4.0,
                  since_ms: int | None = None) -> list[list]:
    start = since_ms if since_ms is not None else int(
        (time.time() - years * 365 * 24 * 3600) * 1000)
    rows: list[list] = []
    while True:
        page = _get(FAPI, "/fapi/v1/fundingRate",
                    {"symbol": symbol, "startTime": str(start), "limit": "1000"})
        if not page:
            break
        rows.extend([[int(r["fundingTime"]), r["fundingRate"]] for r in page])
        last = int(page[-1]["fundingTime"])
        if len(page) < 1000 or last >= (time.time() - 8 * 3600) * 1000:
            break
        start = last + 1
        time.sleep(SLEEP)
    rows.sort(key=lambda r: r[0])
    return rows


def fetch_premium(symbol: str, years: float = 2.0,
                  since_ms: int | None = None) -> list[list]:
    """Premium index history (5m points). ~500/page; samples the direct
    causal input Binance uses to compute funding — better than the
    perp-spot basis reconstruction in the dataset builder."""
    start = since_ms if since_ms is not None else int(
        (time.time() - years * 365 * 24 * 3600) * 1000)
    rows: list[list] = []
    while True:
        page = _get(FAPI, "/futures/data/premiumIndexHistory",
                    {"symbol": symbol, "startTime": str(start), "limit": "500"})
        if not page:
            break
        rows.extend([[int(r["time"]), r["open"], r["close"]] for r in page])
        last = int(page[-1]["time"])
        print(f"  {symbol} premium: {len(rows)} points "
              f"(to {time.strftime('%Y-%m-%d', time.gmtime(last / 1000))})", end="\r")
        if len(page) < 500 or last >= (time.time() - 3600) * 1000:
            break
        start = last + 1
        time.sleep(SLEEP)
    print()
    rows.sort(key=lambda r: r[0])
    return rows


def _get_last_ts(path: Path) -> int | None:
    """Most recent timestamp in a CSV, or None if file missing/empty.
    Assumes ascending row order, which every writer here guarantees."""
    if not path.exists():
        return None
    last = None
    with open(path, "r", newline="") as f:
        for row in csv.reader(f):
            if row:
                last = row[0]
    return int(last) if last is not None else None


def _append(path: Path, header: list[str], new_rows: list[list]) -> int:
    """Merge new_rows into path, deduping on the ts column; returns rows
    actually added. Binance startTime is inclusive, so a since_ms fetch
    re-returns the last saved row — the dedupe drops it rather than
    duplicating. Merge-rewrite (not blind append) so a short page can
    never clobber the history already on disk."""
    if not new_rows:
        return 0
    existing: list[list] = []
    if path.exists():
        with open(path, "r", newline="") as f:
            r = csv.reader(f)
            next(r, None)                     # header
            existing = [row for row in r if row]
    seen = {str(row[0]) for row in existing}
    added = [row for row in new_rows if str(row[0]) not in seen]
    if added:
        _write(path, header, existing + added)
    return len(added)


def _write(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  wrote {path.relative_to(OUT)} ({len(rows)} rows)")


def main() -> int:
    cron_mode = os.environ.get("CRON") == "1"

    ap = argparse.ArgumentParser()
    ap.add_argument("--funding-years", type=float, default=4.0)
    ap.add_argument("--candle-years", type=float, default=2.0)
    ap.add_argument("--symbols", type=str, default="",
                    help="comma list override, e.g. BTC for a smoke test")
    ap.add_argument("--no-premium", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or SYMBOLS

    if cron_mode:
        # Daily archiver: incremental fetch per symbol — only bars newer
        # than the last saved timestamp, merged with dedupe via _append.
        # A missing file gets its full --funding-years/--candle-years
        # history on first sight, so one entry point both bootstraps and
        # maintains the archive.
        print("daily archiver mode (CRON=1): incremental fetch since last saved bar")
        for sym in syms:
            print(f"{sym}:")
            try:
                f_fund = OUT / f"{sym}_funding.csv"
                f_perp = OUT / f"{sym}_perp_1h.csv"
                f_spot = OUT / f"{sym}_spot_1h.csv"
                n = _append(f_fund, ["ts", "rate"],
                            fetch_funding(sym + "USDT", args.funding_years,
                                          since_ms=_get_last_ts(f_fund)))
                print(f"  funding +{n}")
                n = _append(f_perp, ["ts", "open", "high", "low", "close", "vol"],
                            fetch_klines(FAPI, "/fapi/v1/klines", sym + "USDT",
                                         args.candle_years, 1500,
                                         since_ms=_get_last_ts(f_perp)))
                print(f"  perp +{n}")
                n = _append(f_spot, ["ts", "open", "high", "low", "close", "vol"],
                            fetch_klines(SPOT, "/api/v3/klines", sym + "USDT",
                                         args.candle_years, 1000,
                                         since_ms=_get_last_ts(f_spot)))
                print(f"  spot +{n}")
                if (not args.no_premium) and sym in PREMIUM_SYMBOLS:
                    f_prem = OUT / f"{sym}_premium.csv"
                    n = _append(f_prem, ["ts", "premium_open", "premium_close"],
                                fetch_premium(sym + "USDT", args.candle_years,
                                              since_ms=_get_last_ts(f_prem)))
                    print(f"  premium +{n}")
            except Exception as e:  # noqa: BLE001 - one bad symbol must not kill the run
                print(f"  {sym}: FAILED ({e}) — others continue")
    else:
        # Interactive / full-fetch mode: overwrite per symbol (existing behaviour)
        for sym in syms:
            f_fund = OUT / f"{sym}_funding.csv"
            f_perp = OUT / f"{sym}_perp_1h.csv"
            f_spot = OUT / f"{sym}_spot_1h.csv"
            f_prem = OUT / f"{sym}_premium.csv"
            want_prem = (not args.no_premium) and sym in PREMIUM_SYMBOLS
            cached = all(p.exists() for p in (f_fund, f_perp, f_spot,
                                              *((f_prem,) if want_prem else ())))
            if cached and not args.refresh:
                print(f"{sym}: cached")
                continue
            print(f"{sym}: fetching")
            try:
                _write(f_fund, ["ts", "rate"],
                       fetch_funding(sym + "USDT", args.funding_years))
                _write(f_perp, ["ts", "open", "high", "low", "close", "vol"],
                       fetch_klines(FAPI, "/fapi/v1/klines", sym + "USDT",
                                    args.candle_years, 1500))
                _write(f_spot, ["ts", "open", "high", "low", "close", "vol"],
                       fetch_klines(SPOT, "/api/v3/klines", sym + "USDT",
                                    args.candle_years, 1000))
                if want_prem:
                    _write(f_prem, ["ts", "premium_open", "premium_close"],
                           fetch_premium(sym + "USDT", args.candle_years))
            except Exception as e:  # noqa: BLE001 - one bad symbol must not kill the run
                print(f"  {sym}: FAILED ({e}) — skipping, others continue")

    # delisted cohort: funding only, into delisted/
    print("delisted cohort (funding only):")
    for sym in DELISTED:
        f_fund = OUT / "delisted" / f"{sym}_funding.csv"
        if f_fund.exists() and not args.refresh:
            print(f"  {sym}: cached")
            continue
        try:
            rows = fetch_funding(sym + "USDT", args.funding_years)
            if not rows:
                print(f"  {sym}: no history served (endpoint kept nothing)")
                continue
            _write(f_fund, ["ts", "rate"], rows)
        except Exception as e:  # noqa: BLE001
            print(f"  {sym}: FAILED ({e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
