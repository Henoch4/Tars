"""Injective vs OKX funding comparison — offline research, read-only.

Compares Injective perp funding rates against OKX (the venue the satellite
trades) to assess whether Injective funding is a usable cross-venue proxy.
Reads public endpoints only — no keys, no state changes, no impact on the
running satellite or G1 window.

Key constraint discovered during scoping:
  - OKX /api/v5/public/funding-rate-history returns ~3 months directly,
    2 years via Binance fapi proxy (pattern already proven in
    scripts/fetch_history.py::fetch_funding_binance).
  - Injective InjectiveClient.get_funding_rate() returns only the LATEST
    rate via gRPC FundingRates(limit=1) — there is no public historical
    funding endpoint exposed by the client. Historical Injective funding
    depth is therefore a research gap, not a blocker for this comparison.

Outputs:
  - Console summary: latest rates per symbol, OKX/Injective divergence,
    OKX/Binance correlation on overlap.
  - data/funding_comparison_YYYYMMDD.json — machine-readable results.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
DATA.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTC", "ETH", "SOL", "BNB"]
OKX_BASE = "https://www.okx.com"
BINANCE_BASE = "https://fapi.binance.com"
RATE_SLEEP_S = 0.15
MAX_RETRIES = 4


# ---------------------------------------------------------------------------
# OKX funding (public, ~3 months direct, proven in fetch_history.py)
# ---------------------------------------------------------------------------

def _get(path: str, base: str, params: dict[str, str]) -> Any:
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{base}{path}?{qs}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (AuditTrailTrader-funding-research/1.0)",
    })
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode())
            if isinstance(payload, dict) and payload.get("code") != "0":
                print(f"  {base} code={payload.get('code')} msg={payload.get('msg')}; retrying")
                time.sleep(1.0 * (attempt + 1))
                continue
            return payload.get("data", payload if isinstance(payload, list) else [])
        except Exception as e:
            print(f"  request error: {e}; retrying")
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"failed after {MAX_RETRIES} attempts: {path}")


def fetch_okx_funding(inst_id: str) -> list[list[str]]:
    """OKX funding-rate-history (8h realizations, ~3 months direct)."""
    rows: list[list[str]] = []
    after = int(time.time() * 1000)
    while True:
        page = _get("/api/v5/public/funding-rate-history", OKX_BASE,
                    {"instId": inst_id, "limit": "100", "after": str(after)})
        if not page:
            break
        rows.extend(page)
        oldest = int(page[-1]["fundingTime"])
        if oldest <= int(time.time() * 1000) - 90 * 24 * 3600 * 1000 or len(page) < 100:
            break
        after = oldest
        time.sleep(RATE_SLEEP_S)
    rows.sort(key=lambda r: int(r["fundingTime"]))
    return [[r["fundingTime"], r["fundingRate"], r.get("realizedRate", "")] for r in rows]


# ---------------------------------------------------------------------------
# Binance funding (public, full-depth proxy — proven in fetch_history.py)
# ---------------------------------------------------------------------------

def fetch_binance_funding(symbol: str) -> list[list[str]]:
    """Binance USDT-perp fundingRate history (no auth, ~2 years)."""
    rows: list[list[str]] = []
    start = int(time.time() * 1000) - 365 * 2 * 24 * 3600 * 1000
    while True:
        qs = f"symbol={symbol}&startTime={start}&limit=1000"
        req = urllib.request.Request(f"{BINANCE_BASE}/fapi/v1/fundingRate?{qs}",
                                     headers={"User-Agent": "AuditTrailTrader-funding-research/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            page = json.loads(resp.read().decode())
        if not page:
            break
        rows.extend(page)
        last_ts = int(page[-1]["fundingTime"])
        if last_ts >= int(time.time() * 1000) - 8 * 3600 * 1000 or len(page) < 1000:
            break
        start = last_ts + 1
        time.sleep(RATE_SLEEP_S)
    rows.sort(key=lambda r: int(r["fundingTime"]))
    return [[str(int(r["fundingTime"])), str(r["fundingRate"])] for r in rows]


# ---------------------------------------------------------------------------
# Injective funding (gRPC, latest only — no historical endpoint in client)
# ---------------------------------------------------------------------------

def fetch_injective_latest(asset: str = "BTC-USDT-SWAP") -> dict[str, Any]:
    """Latest Injective funding rate via gRPC (read-only, no key).

    Returns None if grpc/pyinjective deps are missing or the call fails —
    this is expected in offline-only environments; the comparison still
    proceeds with OKX+Binance.
    """
    try:
        import grpc  # noqa: F401  (heavy dep, imported lazily)
    except ImportError:
        return {"error": "grpcio not installed — Injective live read skipped"}

    try:
        import asyncio
        import sys
        sys.path.insert(0, str(REPO))  # repo root, so `src` is a package
        from src.injective_client import InjectiveClient, InjectiveConfig

        async def _fetch() -> dict[str, Any]:
            client = InjectiveClient(InjectiveConfig())
            try:
                rate = await client.get_funding_rate(asset, timeout=10.0)
                return {"asset": asset, "funding_rate": rate.get("fundingRate", "0"),
                        "source": "injective_grpc_latest"}
            finally:
                await client.close()

        return asyncio.run(_fetch())
    except Exception as e:  # noqa: BLE001 — research script, report and continue
        return {"error": f"Injective fetch failed: {type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 2:
        return None
    mx = sum(xs[:n]) / n
    my = sum(ys[:n]) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    dx = sum((x - mx) ** 2 for x in xs[:n]) ** 0.5
    dy = sum((y - my) ** 2 for y in ys[:n]) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def compare(symbol: str, okx: list[list[str]], binance: list[list[str]],
            injective: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"symbol": symbol}

    okx_rates = [(_to_float(r[0]), _to_float(r[1])) for r in okx if _to_float(r[1]) is not None]
    binance_rates = [(_to_float(r[0]), _to_float(r[0]) and _to_float(r[1])) for r in binance
                     if _to_float(r[1]) is not None]
    binance_rates = [(t, r) for t, r in binance_rates if r is not None]

    result["okx_rows"] = len(okx_rates)
    result["binance_rows"] = len(binance_rates)
    result["injective"] = injective

    if okx_rates:
        latest_okx = okx_rates[-1]
        result["okx_latest_ts"] = latest_okx[0]
        result["okx_latest_rate"] = latest_okx[1]
        result["okx_min"] = min(r[1] for r in okx_rates)
        result["okx_max"] = max(r[1] for r in okx_rates)
        result["okx_mean"] = sum(r[1] for r in okx_rates) / len(okx_rates)

    if binance_rates:
        latest_bn = binance_rates[-1]
        result["binance_latest_ts"] = latest_bn[0]
        result["binance_latest_rate"] = latest_bn[1]

    # Cross-venue correlation on overlapping timestamps
    okx_by_ts = {t: r for t, r in okx_rates}
    bn_by_ts = {t: r for t, r in binance_rates}
    common_ts = sorted(set(okx_by_ts) & set(bn_by_ts))
    if len(common_ts) >= 2:
        xs = [okx_by_ts[t] for t in common_ts]
        ys = [bn_by_ts[t] for t in common_ts]
        result["overlap_rows"] = len(common_ts)
        result["okx_binance_correlation"] = _pearson(xs, ys)
        result["overlap_first_ts"] = common_ts[0]
        result["overlap_last_ts"] = common_ts[-1]

    # Injective vs OKX latest (single point — directional only)
    if injective.get("funding_rate") is not None and result.get("okx_latest_rate") is not None:
        inj_rate = float(injective["funding_rate"])
        okx_rate = result["okx_latest_rate"]
        result["injective_latest_rate"] = inj_rate
        result["injective_vs_okx_latest_bps"] = (inj_rate - okx_rate) * 10000

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare Injective vs OKX funding rates (read-only)")
    ap.add_argument("--symbols", default="BTC,ETH,SOL,BNB", help="comma-separated symbols")
    ap.add_argument("--no-injective", action="store_true",
                    help="skip Injective live fetch (offline-only mode)")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]

    print("=== Injective vs OKX funding comparison (read-only research) ===")
    print(f"Symbols: {symbols}")
    print(f"OKX source: /api/v5/public/funding-rate-history (~3 months direct)")
    print(f"Binance source: /fapi/v1/fundingRate (~2 years proxy)")
    print(f"Injective source: gRPC FundingRates(limit=1) — latest only")
    print()

    results: list[dict[str, Any]] = []

    for sym in symbols:
        swap = f"{sym}-USDT-SWAP"
        print(f"--- {swap} ---")

        # OKX funding
        try:
            okx = fetch_okx_funding(swap)
            print(f"  OKX: {len(okx)} rows")
        except Exception as e:
            print(f"  OKX: FAILED — {e}")
            okx = []

        # Binance funding (proxy)
        try:
            bn = fetch_binance_funding(f"{sym}USDT")
            print(f"  Binance: {len(bn)} rows")
        except Exception as e:
            print(f"  Binance: FAILED — {e}")
            bn = []

        # Injective latest
        if args.no_injective:
            inj = {"skipped": True}
            print("  Injective: skipped (--no-injective)")
        else:
            inj = fetch_injective_latest(swap)
            if "error" in inj:
                print(f"  Injective: FAILED — {inj['error']}")
            elif "skipped" in inj:
                print("  Injective: skipped")
            else:
                print(f"  Injective latest: {inj['funding_rate']} ({inj['source']})")

        result = compare(sym, okx, bn, inj)
        results.append(result)

        # Print per-symbol summary
        print(f"  OKX latest: {result.get('okx_latest_rate')} (n={result['okx_rows']})")
        if result.get("binance_latest_rate") is not None:
            print(f"  Binance latest: {result['binance_latest_rate']} (n={result['binance_rows']})")
        if result.get("okx_binance_correlation") is not None:
            print(f"  OKX-Binance correlation (n={result['overlap_rows']}): "
                  f"{result['okx_binance_correlation']:+.4f}")
        if result.get("injective_vs_okx_latest_bps") is not None:
            print(f"  Injective vs OKX latest: {result['injective_vs_okx_latest_bps']:+.2f} bps")
        print()

    # Write machine-readable results
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "results": results,
        "notes": [
            "Injective client exposes only latest funding rate (gRPC limit=1); no historical endpoint.",
            "OKX direct funding history capped at ~3 months; Binance used as 2-year cross-venue proxy.",
            "OKX-Binance correlation on overlap indicates proxy reliability.",
            "Injective vs OKX latest is a single data point — directional only, not statistical evidence.",
        ],
    }
    out_path = DATA / f"funding_comparison_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Results written to {out_path}")

    # Summary table
    print()
    print("=== Summary ===")
    print(f"{'Symbol':<8} {'OKX latest':<14} {'Binance latest':<16} {'Inj vs OKX bps':<16} {'OKX-BN corr':<12}")
    for r in results:
        inj_vs = f"{r.get('injective_vs_okx_latest_bps', 0):+.2f}" if r.get("injective_vs_okx_latest_bps") is not None else "n/a"
        corr = f"{r.get('okx_binance_correlation', 0):+.4f}" if r.get("okx_binance_correlation") is not None else "n/a"
        print(f"{r['symbol']:<8} {r.get('okx_latest_rate','n/a'):<14} "
              f"{r.get('binance_latest_rate','n/a'):<16} {inj_vs:<16} {corr:<12}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
