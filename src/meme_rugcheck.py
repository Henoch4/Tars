"""RugCheck feed (free, keyless, VPS-reachable): creator + rugged verdict.

Supplies what Helius/DexScreener cannot: the deployer address and an
independent rugged determination. Fail-soft by design: timeouts/HTTP
errors/unknown shapes -> all-None (filter SKIP-defaults, behavior
unchanged). Never fabricates: creator_hold_pct is computed only when the
creator appears in the reported holder list with a numeric pct.

Rate discipline: one GET per mint per enrichment; 10s timeout; no retries
(the loop re-discovers every cycle — a missed read retries next round).
"""
from __future__ import annotations

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

REPORT_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
TIMEOUT_S = 10.0


class RugCheckError(RuntimeError):
    """Typed fetch failure: the caller SKIP-defaults, never guesses."""


def _http_get(url: str, timeout_s: float = TIMEOUT_S) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "tars-satellite/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception as e:
        raise RugCheckError(f"fetch failed: {type(e).__name__}: {e}") from e
    try:
        data = json.loads(body)
    except ValueError as e:
        raise RugCheckError(f"non-JSON report: {e}") from e
    if not isinstance(data, dict):
        raise RugCheckError("report is not an object")
    return data


def rugcheck_mint(mint: str, get=None) -> dict:
    """Fetch + normalize one RugCheck report. Always returns the same keys;
    unknown/absent -> None (never zero, never guessed)."""
    if not isinstance(mint, str) or not mint.strip():
        raise RugCheckError(f"invalid mint {mint!r}")
    get = get or _http_get
    try:
        data = get(REPORT_URL.format(mint=mint.strip()))
    except RugCheckError:
        raise
    except Exception as e:
        raise RugCheckError(f"fetch failed: {type(e).__name__}: {e}") from e
    if not isinstance(data, dict):
        raise RugCheckError("report is not an object")

    creator = data.get("creator")
    if not isinstance(creator, str) or not creator:
        creator = None
    rugged = data.get("rugged")
    rugged = rugged if isinstance(rugged, bool) else None
    score = data.get("score")
    try:
        score = float(score)  # type: ignore[arg-type]  # guarded by except below
    except (TypeError, ValueError):
        score = None
    risks = data.get("risks")
    if isinstance(risks, list):
        risk_names = sorted(
            str(r.get("name")) for r in risks
            if isinstance(r, dict) and r.get("name"))
    else:
        risk_names = None

    creator_pct = None
    holders = data.get("topHolders")
    if isinstance(holders, list) and creator:
        for h in holders:
            if isinstance(h, dict) and h.get("address") == creator:
                try:
                    creator_pct = float(h.get("pct"))  # type: ignore[arg-type]  # guarded below
                    break
                except (TypeError, ValueError):
                    creator_pct = None
                    break

    return {
        "mint": mint.strip(),
        "creator_address": creator,
        "creator_hold_pct": creator_pct,
        "rugcheck_rugged": rugged,
        "rugcheck_score": score,
        "rugcheck_risks": risk_names,
    }
