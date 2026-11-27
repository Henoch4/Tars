"""Read-only Solana simulation and sellability result boundary."""
from __future__ import annotations

import base64
import json
import math
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class SellabilityResult:
    status: str  # passed, blocked, unavailable
    effective_tax_pct: float | None = None
    token_in_raw: int | None = None
    token_out_raw: int | None = None
    simulation_error: str | None = None
    source: str = "simulation"
    provenance: dict[str, str] = field(default_factory=dict)


def build_unsigned_swap(
    input_mint: str,
    output_mint: str,
    amount_raw: int,
    taker: str,
    *,
    slippage_bps: int = 100,
    quote: Callable[..., dict] | None = None,
) -> dict[str, Any]:
    """Build (but never sign) a Jupiter swap transaction for simulation.

    ``taker`` is a public wallet address only. The returned transaction is
    validated base64 and can be passed to :class:`SolanaSimulationClient`;
    no keypair or broadcast endpoint is touched.
    """
    if quote is None:
        from .meme_execute import quote_swap
        quote = quote_swap
    order = quote(input_mint, output_mint, amount_raw, taker,
                  slippage_bps=slippage_bps)
    if not isinstance(order, dict):
        raise ValueError("swap builder returned a non-object")
    tx = order.get("transaction")
    if not isinstance(tx, str) or not tx:
        raise ValueError("swap builder returned no unsigned transaction")
    try:
        base64.b64decode(tx, validate=True)
    except Exception as exc:
        raise ValueError("swap builder returned invalid transaction base64") from exc
    out_amount = order.get("outAmount")
    return {
        "transaction": tx,
        "out_amount_raw": int(out_amount) if isinstance(out_amount, (int, str)) and str(out_amount).isdigit() else None,
        "provenance": {"provider": "jupiter", "method": "swap_v2_order",
                        "signed": "false", "broadcast": "false"},
    }


def simulate_unsigned_round_trip(
    *,
    quote_mint: str,
    token_mint: str,
    quote_amount_raw: int,
    taker: str,
    simulation: "SolanaSimulationClient",
    slippage_bps: int = 100,
    quote: Callable[..., dict] | None = None,
) -> SellabilityResult:
    """Build and simulate both legs without signing or broadcasting.

    The returned result is explicitly marked as quote-output evidence. It is
    useful for sellability gating, but callers must not promote it to an
    authoritative transfer-tax value without observed token balances.
    """
    provenance = {
        "provider": "jupiter+solana_rpc",
        "buy": "unsigned_simulateTransaction",
        "sell": "unsigned_simulateTransaction",
        "signed": "false", "broadcast": "false",
        "measurement": "quote_outputs_verified_by_simulation",
    }
    try:
        buy = build_unsigned_swap(quote_mint, token_mint, quote_amount_raw,
                                  taker, slippage_bps=slippage_bps, quote=quote)
        buy_sim = simulation.simulate_transaction(buy["transaction"])
        if not buy_sim.get("ok"):
            return SellabilityResult("blocked", simulation_error=f"buy:{buy_sim.get('error') or buy_sim.get('err')}", provenance=provenance)
        token_amount = buy.get("out_amount_raw")
        if not isinstance(token_amount, int) or token_amount <= 0:
            return SellabilityResult("unavailable", simulation_error="buy output amount unavailable", provenance=provenance)
        sell = build_unsigned_swap(token_mint, quote_mint, token_amount,
                                   taker, slippage_bps=slippage_bps, quote=quote)
        sell_sim = simulation.simulate_transaction(sell["transaction"])
        if not sell_sim.get("ok"):
            return SellabilityResult("blocked", simulation_error=f"sell:{sell_sim.get('error') or sell_sim.get('err')}", provenance=provenance)
        quote_back = sell.get("out_amount_raw")
        if not isinstance(quote_back, int) or quote_back < 0:
            return SellabilityResult("unavailable", simulation_error="sell output amount unavailable", provenance=provenance)
        result = assess_round_trip(quote_amount_raw, quote_back)
        return SellabilityResult(result.status, result.effective_tax_pct,
                                 result.token_in_raw, result.token_out_raw,
                                 result.simulation_error, "simulation", provenance)
    except Exception as exc:
        return SellabilityResult("unavailable", simulation_error=str(exc), provenance=provenance)


def assess_round_trip(token_in_raw: int, token_out_raw: int) -> SellabilityResult:
    """Measure the observed loss without treating missing amounts as zero."""
    if (isinstance(token_in_raw, bool) or isinstance(token_out_raw, bool)
            or not isinstance(token_in_raw, int)
            or not isinstance(token_out_raw, int)
            or token_in_raw <= 0 or token_out_raw < 0):
        return SellabilityResult("unavailable", simulation_error="invalid amounts")
    effective = (1.0 - token_out_raw / token_in_raw) * 100.0
    if not math.isfinite(effective):
        return SellabilityResult("unavailable", simulation_error="non-finite result")
    effective = round(max(0.0, min(100.0, effective)), 6)
    if token_out_raw == 0:
        return SellabilityResult(
            "blocked", effective_tax_pct=100.0,
            token_in_raw=token_in_raw, token_out_raw=token_out_raw,
            simulation_error="sell simulation returned zero output",
        )
    return SellabilityResult(
        "passed", effective_tax_pct=effective,
        token_in_raw=token_in_raw, token_out_raw=token_out_raw,
    )


def assess_simulated_round_trip(
    buy_result: dict[str, Any] | None,
    sell_result: dict[str, Any] | None,
    token_in_raw: int,
    token_out_raw: int,
    *,
    provider: str = "solana_rpc",
) -> SellabilityResult:
    """Convert two read-only simulation responses into attributed evidence.

    Simulation errors are never interpreted as zero tax. A failed buy or sell
    is ``blocked`` only when the RPC explicitly reports a transaction error;
    missing/malformed responses remain ``unavailable`` and therefore fail
    closed upstream.
    """
    base = {"provider": provider, "method": "simulateTransaction",
            "buy": "simulated", "sell": "simulated"}
    if not isinstance(buy_result, dict) or not isinstance(sell_result, dict):
        return SellabilityResult("unavailable", simulation_error="missing simulation result",
                                 source="simulation", provenance=base)
    if buy_result.get("ok") is False:
        return SellabilityResult("blocked", simulation_error=f"buy:{buy_result.get('error') or buy_result.get('err')}",
                                 source="simulation", provenance=base)
    if sell_result.get("ok") is False:
        return SellabilityResult("blocked", simulation_error=f"sell:{sell_result.get('error') or sell_result.get('err')}",
                                 source="simulation", provenance=base)
    result = assess_round_trip(token_in_raw, token_out_raw)
    return SellabilityResult(
        result.status, result.effective_tax_pct, result.token_in_raw,
        result.token_out_raw, result.simulation_error, "simulation", base,
    )


class SolanaSimulationClient:
    """Minimal JSON-RPC ``simulateTransaction`` client.

    It accepts an already-built, unsigned or signed transaction payload and
    never signs, sends, or persists it. Callers must supply the transaction
    from a separate quote/build adapter and inspect the returned RPC logs.
    """

    def __init__(self, rpc_url: str, *, post: Callable[[dict], dict] | None = None):
        self.rpc_url = rpc_url
        self._post = post or self._http_post

    def _http_post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.rpc_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode())

    def simulate_transaction(self, transaction_b64: str) -> dict[str, Any]:
        if not isinstance(transaction_b64, str) or not transaction_b64:
            raise ValueError("transaction must be base64 text")
        try:
            base64.b64decode(transaction_b64, validate=True)
        except Exception as exc:
            raise ValueError("transaction is not valid base64") from exc
        response = self._post({
            "jsonrpc": "2.0", "id": 1, "method": "simulateTransaction",
            "params": [transaction_b64, {
                "encoding": "base64", "sigVerify": False,
                "replaceRecentBlockhash": True,
                "commitment": "processed",
            }],
        })
        if not isinstance(response, dict):
            raise ValueError("simulation response is not an object")
        if response.get("error"):
            return {"ok": False, "error": response["error"]}
        value = ((response.get("result") or {}).get("value")
                 if isinstance(response.get("result"), dict) else None)
        if not isinstance(value, dict):
            return {"ok": False, "error": "simulation result missing value"}
        return {
            "ok": value.get("err") in (None, False),
            "err": value.get("err"),
            "logs": value.get("logs") or [],
            "units_consumed": value.get("unitsConsumed"),
            "post_token_balances": value.get("postTokenBalances") or [],
            "pre_token_balances": value.get("preTokenBalances") or [],
        }
