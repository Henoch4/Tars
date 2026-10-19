"""Jupiter round-trip sellability probe (Book-2 Gate 4 measurement).

A static "buy/sell tax" field has no honest supplier on Solana — the
truth is what a real swap would settle. This quotes the actual Jupiter
route twice at entry size ($25): mint->SOL, then SOL->mint with the
received lamports, and reports the round-trip cost in percent of
notional. A honeypot / transfer-hook trap surfaces as a failed or
total-loss sell leg; a healthy pool shows pool fee + price impact
(~1-3% at $25 into a $20k+ pool). Read-only: quotes are free, unsigned,
and broadcast nothing.

Fail-soft by contract: any transport/shape failure -> None, which the
filter treats as missing_data (rejects more, never less). A FAILED SELL
leg after a successful buy leg is NOT a failure — it is the measurement
itself (cost = 100%, unsellable).

Hosts/paths/headers are shared with the signed execute path
(meme_execute) — verified keyless from the VPS 2026-10-17 (api.jup.ag
price/v3 + swap/v2/order return 200 with no API key; the lite-api swap
tier 429s, so it is NOT used here).

`token_info()` additionally reads the keyless /tokens/v2/search profile
(dev wallet, dev hold %, organic score) used as a fallback supplier for
creator_hold_pct and as an informational organic/bundled note. It returns
an ARRAY whose shape _http rejects, so it carries its own transport.
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request

from src.meme_execute import PRICE_URL, SWAP_BASE, _headers, _http

TOKEN_URL = "https://api.jup.ag/tokens/v2/search"
SOL_MINT = "So11111111111111111111111111111111111111112"

# Keyless /tokens/v2/search is hard-limited (~5 requests, then HTTP 429 —
# measured live 2026-10-19). A short retry keeps a 2-3-graduate cycle from
# losing its creator address to a transient 429.
TOKEN_RETRY_ATTEMPTS = 3
TOKEN_RETRY_SLEEP_S = 1.0


def _get_json(url: str, timeout_s: float = 20.0):
    """GET any JSON shape (list OR dict). meme_execute._http asserts an
    object body, but /tokens/v2/search returns an ARRAY — so the token
    supplier needs its own transport. Same browser headers (Jupiter 403s
    the default urllib UA from datacenter IPs)."""
    req = urllib.request.Request(url, method="GET", headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode())
ROUND_TRIP_USD = 25.0   # matches the satellite entry size
SLIPPAGE_BPS = 100      # matches the execute path's default collar
UNSELLABLE_PCT = 100.0  # sell-leg failure/zero-out: the trap itself


def _order(input_mint: str, output_mint: str, amount_raw: int,
           http) -> dict:
    """GET /swap/v2/order (quote form: no taker -> no transaction, just
    the route + outAmount + price impact)."""
    qs = urllib.parse.urlencode({
        "inputMint": input_mint, "outputMint": output_mint,
        "amount": amount_raw, "slippageBps": SLIPPAGE_BPS,
    })
    return http("GET", f"{SWAP_BASE}/order?{qs}")


def _mint_price_decimals(mint: str, http) -> tuple:
    """(decimals, usd_price|None) from Jupiter price v3 — publishes mint
    decimals alongside the price, so one call yields both. The response
    is FLAT ({mint: {...}}); a legacy/tier {"data": {...}} wrapper is
    tolerated too (verified live 2026-10-17: no wrapper)."""
    out = http("GET", f"{PRICE_URL}?ids={urllib.parse.quote(mint)}")
    root = out if isinstance(out, dict) else {}
    if isinstance(root.get("data"), dict):
        root = root["data"]
    data = root.get(mint) or {}
    dec = data.get("decimals")
    if isinstance(dec, bool) or not isinstance(dec, int) or not (0 <= dec <= 12):
        raise ValueError("decimals missing")
    usd = data.get("usdPrice")
    if isinstance(usd, bool) or not isinstance(usd, (int, float)):
        return dec, None
    usd = float(usd)
    return dec, (usd if math.isfinite(usd) and usd > 0 else None)


def round_trip_cost_pct(mint: str, price_usd=None, http=None) -> float | None:
    """Cost of buying ROUND_TRIP_USD of `mint` and selling it back, as a
    percent of notional. None = could not measure (caller fail-closes).
    `price_usd` is the DexScreener fallback when Jupiter's price is absent."""
    if not isinstance(mint, str) or not mint.strip() or mint == SOL_MINT:
        return None
    call = http or _http
    try:
        decimals, jup_price = _mint_price_decimals(mint, call)
    except Exception:
        return None
    price = jup_price
    if price is None:
        if isinstance(price_usd, bool) or not isinstance(price_usd, (int, float)):
            return None
        price = float(price_usd)
    if not math.isfinite(price) or price <= 0:
        return None
    amount_raw = int(ROUND_TRIP_USD / price * (10 ** decimals))
    if amount_raw <= 0:
        return None
    try:
        buy = _order(mint, SOL_MINT, amount_raw, call)
        out_lamports = int(buy.get("outAmount") or 0)
    except Exception:
        return None  # buy leg unmeasurable — not a trap verdict
    if out_lamports <= 0:
        return None
    try:
        sell = _order(SOL_MINT, mint, out_lamports, call)
        back_raw = int(sell.get("outAmount") or 0)
    except Exception:
        return UNSELLABLE_PCT  # sell route refused: the measurement
    if back_raw <= 0:
        return UNSELLABLE_PCT
    cost = (1.0 - back_raw / amount_raw) * 100.0
    return max(0.0, min(UNSELLABLE_PCT, cost))


def _num_or_none(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _int_or_none(v):
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v


def token_info(mint: str, http=None, _sleep=time.sleep) -> dict:
    """Keyless Jupiter token profile (GET /tokens/v2/search?query=).
    Supplies the creator's wallet + DEV HOLD % + organic score — used by
    the runner as a fallback data source for `creator_hold_pct` (Gate 3b)
    and as an informational organic/bundled read. Returns {} on ANY failure
    (fail-soft; callers keep the SKIP-default). Verified live 2026-10-17:
    no wrapper, array body, `audit.devBalancePercentage` / `organicScore`.
    Note: Jupiter's `dev` is the deployer wallet; it is treated as a
    CREATOR proxy, not conflated with RugCheck's creator address.

    Retries a transient 429 up to TOKEN_RETRY_ATTEMPTS with a short sleep
    (the keyless endpoint throttles hard); any other failure returns {} at
    once. `_sleep` is injectable so tests never actually sleep."""
    if not isinstance(mint, str) or not mint.strip():
        return {}
    url = f"{TOKEN_URL}?query={urllib.parse.quote(mint)}"
    out = None
    for attempt in range(TOKEN_RETRY_ATTEMPTS):
        try:
            out = http("GET", url) if http is not None else _get_json(url)
            break
        except Exception as e:
            if "429" in str(e) and attempt < TOKEN_RETRY_ATTEMPTS - 1:
                _sleep(TOKEN_RETRY_SLEEP_S)
                continue
            return {}
    if out is None:
        return {}
    rows = out if isinstance(out, list) else None
    if rows is None and isinstance(out, dict):
        rows = out.get("data") if isinstance(out.get("data"), list) else None
    if not isinstance(rows, list) or not rows:
        return {}
    row = next((r for r in rows
                if isinstance(r, dict) and r.get("id") == mint), None)
    if row is None:
        row = rows[0] if isinstance(rows[0], dict) else None
    if row is None or not isinstance(row, dict):
        return {}
    raw_audit = row.get("audit")
    audit: dict = raw_audit if isinstance(raw_audit, dict) else {}
    return {
        "dev": row.get("dev") if isinstance(row.get("dev"), str) else None,
        "dev_hold_pct": _num_or_none(audit.get("devBalancePercentage")),
        "dev_mints": _int_or_none(audit.get("devMints")),
        "top_holders_pct": _num_or_none(audit.get("topHoldersPercentage")),
        "organic_score": _num_or_none(row.get("organicScore")),
        "organic_label": (row.get("organicScoreLabel")
                          if isinstance(row.get("organicScoreLabel"), str)
                          else None),
        "holder_count": _int_or_none(row.get("holderCount")),
    }
