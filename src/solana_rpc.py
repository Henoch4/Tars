"""Solana data paths (7.1 sidetrack + Solami bounty).

Tarstrade is EVM X Layer + OKX CEX; these bridges read Solana market
data (SOL/USD from the on-chain Pyth push account + slot vitality)
through whichever Solana RPC is configured, with per-call metrics per
source. When a provider is unconfigured or erroring, callers keep the
OKX path unchanged (failover, never fallback-forever).

Providers share one core (`_fetch_snapshot`); only the URL source and
the metrics label differ. gRPC/Shredstream: settings keys exist,
consumer deferred — Yellowstone needs a heavier client (proto subs)
than this repo's stdlib HTTP surface. Status reports grpc as
not-implemented rather than faking it.
"""
from __future__ import annotations

import base64
import json
import logging
import struct
import time
import urllib.request

logger = logging.getLogger(__name__)

# Solami Edge RPC shape (official docs 2026-10-09): shared hostname, NOT
# per-key — https://rpc.solami.fast/sol + api_key query param. Regional
# pins (fra/ams/nyc) keep the same shape on a different host.
SOLAMI_RPC_TEMPLATE = "https://rpc.solami.fast/sol?api_key={key}"
# Pyth SOL/USD push price account (mainnet). Overridable per call; the
# binary magic below fails closed if the address ever serves a different
# layout, so a wrong default errors loudly instead of pricing wrongly.
PYTH_SOL_USD_ACCOUNT = "H6ARHf6YXhGYeQfUzQXYJPhJHRKPvQxaohuGQk8F"
PYTH_MAGIC = 0xA1B2C3D4
TIMEOUT_S = 20


class RpcFastError(RuntimeError):
    """Typed RPC Fast failure: callers fall back to the OKX path and count
    source=rpc-fast/result=error. Never a silent default price."""


def _get_settings():
    from .settings import get_settings
    return get_settings()


def configured() -> bool:
    """RPC Fast usable: URL present (key travels inside it or separately)."""
    try:
        url = (_get_settings().rpc_fast_url or "").strip()
    except Exception:
        return False
    return bool(url)


def _post(url: str, payload: dict, timeout_s: float) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            out = json.loads(resp.read().decode())
    except Exception as e:
        raise RpcFastError(f"rpc transport failed: {type(e).__name__}") from e
    if isinstance(out, dict) and out.get("error"):
        raise RpcFastError(f"rpc error: {out['error']}")
    return out


def _parse_pyth_price(data_b64: str) -> float:
    """Pyth v2 price account -> USD. Magic-checked; anything else raises."""
    try:
        raw = base64.b64decode(data_b64)
    except Exception as e:
        raise RpcFastError(f"pyth account not base64: {e}") from e
    if len(raw) < 168:
        raise RpcFastError(f"pyth account too short ({len(raw)} bytes)")
    magic = struct.unpack_from("<I", raw, 0)[0]
    if magic != PYTH_MAGIC:
        raise RpcFastError(
            f"pyth magic mismatch ({magic:#x}): account is not a v2 price feed")
    expo = struct.unpack_from("<i", raw, 20)[0]
    price = struct.unpack_from("<q", raw, 160)[0]
    if abs(expo) > 18:
        raise RpcFastError(f"pyth expo implausible ({expo})")
    return float(price) * (10.0 ** expo)


def fetch_solana_market_snapshot(price_account: str | None = None,
                                 timeout_s: float | None = None) -> dict:
    """SOL/USD + slot via the configured RPC Fast endpoint. Raises
    RpcFastError when unconfigured or on any failure (caller falls back).
    Counts usage + latency in metrics either way."""
    settings = _get_settings()
    url = (getattr(settings, "rpc_fast_url", "") or "").strip()
    if not url:
        raise RpcFastError("RPC_FAST_URL unset — OKX path stays authoritative")
    timeout = timeout_s or float(getattr(settings, "rpc_fast_timeout_seconds", 20) or 20)
    return _fetch_snapshot(url, "rpc-fast", price_account, timeout)


def _resolve_solami_url(settings) -> str:
    """Explicit SOLAMI_RPC_URL wins; else assemble the official Edge shape
    from SOLAMI_API_KEY. Empty when neither exists (caller raises)."""
    explicit = (getattr(settings, "solami_rpc_url", "") or "").strip()
    if explicit:
        return explicit
    key = (getattr(settings, "solami_api_key", "") or "").strip()
    if key:
        return SOLAMI_RPC_TEMPLATE.format(key=key)
    return ""


def solami_configured() -> bool:
    """Solami RPC usable: explicit URL or an API key to assemble it."""
    try:
        return bool(_resolve_solami_url(_get_settings()))
    except Exception:
        return False


def fetch_via_solami(price_account: str | None = None,
                     timeout_s: float | None = None) -> dict:
    """Same snapshot over the Solami Edge RPC (source=solami in metrics).
    Raises RpcFastError when unconfigured or failing. The typed error is
    shared: callers treat every Solana path identically."""
    settings = _get_settings()
    url = _resolve_solami_url(settings)
    if not url:
        raise RpcFastError("SOLAMI_RPC_URL / SOLAMI_API_KEY unset — satellite keeps its fallbacks")
    timeout = timeout_s or 20.0
    return _fetch_snapshot(url, "solami", price_account, timeout)


def _fetch_snapshot(url: str, source: str, price_account: str | None,
                    timeout: float) -> dict:
    from .metrics import inc
    t0 = time.time()
    try:
        slot_out = _post(url, {"jsonrpc": "2.0", "id": 1,
                               "method": "getSlot", "params": []}, timeout)
        slot = slot_out.get("result")
        acct_out = _post(url, {"jsonrpc": "2.0", "id": 2,
                               "method": "getAccountInfo",
                               "params": [price_account or PYTH_SOL_USD_ACCOUNT,
                                          {"encoding": "base64"}]},
                         timeout)
        value = (acct_out.get("result") or {}).get("value") or {}
        data = (value.get("data") or [None])[0]
        if not data:
            raise RpcFastError("pyth account has no data")
        sol_usd = _parse_pyth_price(data)
        latency = time.time() - t0
        inc("tars_rpc_fast_usage_total",
            {"source": source, "result": "ok"})
        return {"source": source, "sol_usd": sol_usd, "slot": slot,
                "latency_s": round(latency, 3)}
    except RpcFastError:
        inc("tars_rpc_fast_usage_total",
            {"source": source, "result": "error"})
        raise
    except Exception as e:  # noqa: BLE001 — any transport surprise is typed
        inc("tars_rpc_fast_usage_total",
            {"source": source, "result": "error"})
        raise RpcFastError(f"fetch failed: {type(e).__name__}: {e}") from e


def rpc_fast_status() -> dict:
    """Status surface for GET /api/v1/rpc-fast-status. Honest-null when
    unconfigured: configured False, everything else empty — never zeros
    dressed as measurements."""
    from .metrics import snapshot
    settings = _get_settings()
    url = (getattr(settings, "rpc_fast_url", "") or "").strip()
    if not url:
        return {"configured": False, "plan": None, "latency_s": None,
                "usage": {}, "grpc": "not-implemented"}
    counters = snapshot().get("counters", {})
    usage = {str(k): v for k, v in counters.items() if "rpc_fast" in str(k)}
    return {
        "configured": True,
        "plan": getattr(settings, "rpc_fast_plan", "") or None,
        "latency_s": None,  # measured per fetch_solana_market_snapshot call
        "usage": usage,
        "grpc": "not-implemented",
    }
