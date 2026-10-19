"""Tiny-live execution for the satellite (Book 2) — Jupiter Swap V2.

Flow per the published API (verified 2026-10-09 against
developers.jup.ag): GET /swap/v2/order (quote + assembled tx, keyless
0.5 RPS) -> sign locally with the Book-2 keypair -> POST /swap/v2/execute
(managed landing, no RPC needed). Price V3 doubles as enrichment.

HARD WALLS:
- Paper by default: quote/simulate only. NOTHING broadcasts unless
  live=True AND an explicit non-empty confirm string is passed (two
  deliberate acts, never a default).
- Cost-hurdle guard: refuses positions whose round-trip cost exceeds
  MAX_COST_FRACTION_OF_SIZE (default 10%) — the 2x-costs doctrine at
  $5 scale, where fees dominate picks.
- Session + per-token caps from meme_satellite (clamp_size), tracked
  across fills in ExecutionLedger. Total Book-2 exposure additionally
  bounded by MAX_SESSION_SPEND_USD.
- Signing needs `solders` (lazy import with a clear error); tests inject
  a stub signer and faked HTTP, never keys or network.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import urllib.parse
import urllib.request

from .meme_satellite import PAPER_GAS_USD, clamp_size

logger = logging.getLogger(__name__)

SWAP_BASE = "https://api.jup.ag/swap/v2"
PRICE_URL = "https://api.jup.ag/price/v3"

MAX_COST_FRACTION_OF_SIZE = 0.10
MAX_SESSION_SPEND_USD = 5.0

SOL_MINT = "So11111111111111111111111111111111111111112"


class SwapError(RuntimeError):
    """Typed swap failure: quote/build/send/confirm problems surface here,
    never as silent fallbacks or fabricated fills."""


def _headers() -> dict:
    key = (os.getenv("JUPITER_API_KEY") or "").strip()
    return {"Accept": "application/json",
            # Jupiter 403s default urllib UA from datacenter IPs (verified
            # on the VPS 2026-10-10; same class as the DexScreener block).
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) TARS-satellite/1.0",
            **({"x-api-key": key} if key else {})}


def _http(method: str, url: str, body: dict | None = None,
          timeout_s: float = 20.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**_headers(),
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            out = json.loads(resp.read().decode())
    except Exception as e:
        raise SwapError(f"jupiter http failed: {type(e).__name__}") from e
    if isinstance(out, dict) and out.get("error"):
        raise SwapError(f"jupiter error: {out['error']}")
    if not isinstance(out, dict):
        raise SwapError("jupiter returned non-object")
    return out


def quote_swap(input_mint: str, output_mint: str, amount_raw: int,
               taker: str, slippage_bps: int = 100,
               http=None) -> dict:
    """GET /swap/v2/order. amount_raw in native units. Returns the order
    (transaction b64 when taker set, requestId, routePlan, fees). Strict
    inputs: bool/stringified/NaN amounts refuse (2.7 discipline)."""
    for name, val in (("input_mint", input_mint), ("output_mint", output_mint),
                      ("taker", taker)):
        if not isinstance(val, str) or not val.strip():
            raise SwapError(f"{name} must be a non-empty string")
    if isinstance(amount_raw, bool) or not isinstance(amount_raw, int):
        raise SwapError(f"amount_raw must be an int of native units, "
                        f"got {amount_raw!r}")
    if amount_raw <= 0:
        raise SwapError(f"amount_raw must be positive, got {amount_raw}")
    if not isinstance(slippage_bps, int) or isinstance(slippage_bps, bool) \
            or slippage_bps < 0:
        raise SwapError(f"slippage_bps must be a non-negative int")
    qs = urllib.parse.urlencode({
        "inputMint": input_mint.strip(), "outputMint": output_mint.strip(),
        "amount": amount_raw, "taker": taker.strip(),
        "slippageBps": slippage_bps,
    })
    call = http or _http
    out = call("GET", f"{SWAP_BASE}/order?{qs}")
    if "requestId" not in out:
        raise SwapError("order response missing requestId")
    return out


def token_prices(mints: list, http=None) -> dict:
    """GET /price/v3?ids= (up to 50 mints). Returns {mint: usd or None}.
    The v3 body is FLAT ({mint: {usdPrice, decimals}}); tolerate a legacy
    {"data": {...}} wrapper too. (Fixed 2026-10-17: unwrapping a
    non-existent "data" key silently None'd every price.)"""
    clean = [m for m in mints if isinstance(m, str) and m.strip()][:50]
    if not clean:
        return {}
    call = http or _http
    out = call("GET", PRICE_URL + "?ids=" + ",".join(clean))
    data = out if isinstance(out, dict) else {}
    if isinstance(data.get("data"), dict):
        data = data["data"]
    prices: dict[str, float | None] = {}
    for m in clean:
        try:
            prices[m] = float(data[m]["usdPrice"])
        except (KeyError, TypeError, ValueError):
            prices[m] = None
    return prices


def book2_pubkey(keypair_path: str) -> str:
    """Public key string for a keypair file (quote taker, Solscan checks).
    Solders-backed like signing; same clear error when absent."""
    return str(load_book2_keypair(keypair_path).pubkey())


def load_book2_keypair(path: str):
    """Load the Book-2 keypair file into a solders Keypair. The file (not
    the key) travels: path only, never contents. Missing solders or a
    malformed file raises with a clear message."""
    try:
        from solders.keypair import Keypair
    except Exception as e:
        raise SwapError(
            "solders missing: pip install solders for live signing") from e
    try:
        secret = bytes(json.loads(open(path, encoding="utf-8").read()))
    except Exception as e:
        raise SwapError(f"cannot read keypair file: {e}") from e
    if len(secret) != 64:
        raise SwapError(f"keypair file must hold 64 ints, got {len(secret)} bytes")
    try:
        return Keypair.from_bytes(secret)
    except Exception as e:
        raise SwapError(f"invalid keypair bytes: {e}") from e


def sign_order_tx(order: dict, keypair, sign_fn=None) -> str:
    """Sign the order's base64 transaction. sign_fn(tx_bytes)->sig_bytes
    override exists for tests; default uses solders (versioned aware)."""
    tx_b64 = order.get("transaction")
    if not tx_b64:
        raise SwapError("order has no transaction (taker probably unset)")
    try:
        tx_bytes = base64.b64decode(tx_b64)
    except Exception as e:
        raise SwapError(f"order transaction not base64: {e}") from e
    if sign_fn is not None:
        sig = sign_fn(tx_bytes)
        return base64.b64encode(sig).decode()
    try:
        from solders.transaction import VersionedTransaction
    except Exception as e:
        raise SwapError(
            "solders missing: pip install solders for live signing") from e
    try:
        tx = VersionedTransaction.from_bytes(tx_bytes)
    except Exception as e:
        raise SwapError(f"cannot parse order transaction: {e}") from e
    try:
        signed = VersionedTransaction(tx.message, [keypair])
    except Exception as e:
        raise SwapError(f"signing failed: {e}") from e
    return base64.b64encode(bytes(signed)).decode()


def execute_signed(signed_tx_b64: str, request_id: str, http=None) -> dict:
    """POST /swap/v2/execute. Returns status/signature/amounts. Anything
    but explicit Success raises (a submitted-but-ambiguous receipt is a
    failure until proven otherwise)."""
    if not signed_tx_b64 or not request_id:
        raise SwapError("execute needs signed transaction + requestId")
    call = http or _http
    out = call("POST", f"{SWAP_BASE}/execute",
               {"signedTransaction": signed_tx_b64, "requestId": request_id})
    if out.get("status") != "Success":
        raise SwapError(f"execute did not succeed: {out}")
    if not out.get("signature"):
        raise SwapError(f"execute success without signature: {out}")
    return out


def round_trip_viable(size_usd: float, expected_costs_usd: float,
                      max_fraction: float = MAX_COST_FRACTION_OF_SIZE) -> tuple:
    """Cost-hurdle guard: (viable, reason). Refuses positions whose costs
    exceed max_fraction of size — at $5 scale most picks fail this, which
    is the guard working, not broken."""
    for name, v in (("size_usd", size_usd), ("expected_costs_usd", expected_costs_usd)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False, f"{name} not a number"
        if not math.isfinite(float(v)) or float(v) < 0:
            return False, f"{name} not finite/non-negative"
    if size_usd <= 0:
        return False, "size must be positive"
    if expected_costs_usd > size_usd * max_fraction:
        return False, (f"costs ${expected_costs_usd:.2f} exceed "
                       f"{max_fraction:.0%} of ${size_usd:.2f} size")
    return True, ""


class ExecutionLedger:
    """Session spend tracking across fills (paper + live share it)."""

    def __init__(self, max_session_usd: float = MAX_SESSION_SPEND_USD,
                 max_cost_fraction: float = MAX_COST_FRACTION_OF_SIZE):
        self.max_session_usd = max_session_usd
        self.max_cost_fraction = max_cost_fraction
        self.spent_usd = 0.0
        self.fills: list = []

    def approve(self, size_usd: float, expected_costs_usd: float) -> tuple:
        """Clamp (per-token/day) + hurdle + session cap, in that order.
        Returns (allowed_size, reason); allowed 0 = no trade."""
        allowed = clamp_size(size_usd, self.spent_usd)
        if allowed <= 0:
            return 0.0, "clamp: token/day cap exhausted"
        ok, reason = round_trip_viable(allowed, expected_costs_usd,
                                       self.max_cost_fraction)
        if not ok:
            return 0.0, f"hurdle: {reason}"
        if self.spent_usd + allowed > self.max_session_usd:
            return 0.0, (f"session cap ${self.max_session_usd:.2f} would "
                         f"break at ${self.spent_usd + allowed:.2f}")
        return allowed, ""

    def record_fill(self, receipt: dict) -> None:
        self.fills.append(receipt)


def live_buy(input_mint: str, output_mint: str, amount_raw: int, taker: str,
             keypair_path: str, expected_costs_usd: float,
             size_usd: float, ledger: ExecutionLedger,
             slippage_bps: int = 100, confirm: str = "",
             http=None, sign_fn=None) -> dict:
    """Full path: approve -> quote -> sign -> execute -> record. Paper
    unless live=True semantics are met: confirm must be a non-empty
    operator string (the second deliberate act). Anything else returns a
    paper quote receipt with broadcast=False and touches nothing."""
    allowed, reason = ledger.approve(size_usd, expected_costs_usd)
    if allowed <= 0:
        return {"broadcast": False, "reason": reason, "allowed_size": 0.0}
    order = quote_swap(input_mint, output_mint, amount_raw, taker,
                       slippage_bps, http=http)
    if not confirm or not confirm.strip():
        return {"broadcast": False, "reason": "no live confirm: paper quote only",
                "allowed_size": allowed, "quote": order}
    keypair = None if sign_fn is not None else load_book2_keypair(keypair_path)
    signed = sign_order_tx(order, keypair, sign_fn=sign_fn)
    receipt = execute_signed(signed, order["requestId"], http=http)
    ledger.record_fill(receipt)
    return {"broadcast": True, "receipt": receipt, "allowed_size": allowed}


def live_sell(input_mint: str, output_mint: str, amount_raw: int, taker: str,
              keypair_path: str, slippage_bps: int = 100, confirm: str = "",
              http=None, sign_fn=None, ledger: "ExecutionLedger | None" = None) -> dict:
    """Exit path (token -> quote asset): quote -> sign -> execute a SELL.

    Reuses the same direction-agnostic quote/sign/execute plumbing as
    live_buy, so input_mint is the token we hold and output_mint the quote
    asset (e.g. SOL_MINT).

    Deliberately does NOT call ExecutionLedger.approve(): that cap bounds
    BUY-side session spend, and an exit FREES capital — an entry cap must
    never be able to block a close. Paper unless confirm is a non-empty
    operator string (the same second-deliberate-act guard as live_buy)."""
    order = quote_swap(input_mint, output_mint, amount_raw, taker,
                       slippage_bps, http=http)
    if not confirm or not confirm.strip():
        return {"broadcast": False, "reason": "no live confirm: paper quote only",
                "quote": order}
    keypair = None if sign_fn is not None else load_book2_keypair(keypair_path)
    signed = sign_order_tx(order, keypair, sign_fn=sign_fn)
    receipt = execute_signed(signed, order["requestId"], http=http)
    if ledger is not None:
        ledger.record_fill(receipt)
    return {"broadcast": True, "receipt": receipt}
