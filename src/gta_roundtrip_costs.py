"""Conservative decomposition of simulated round-trip loss."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class CostBreakdown:
    aggregate_loss_pct: float
    swap_fee_lamports: int | None
    priority_fee_lamports: int | None
    transfer_fee_raw: int | None
    price_impact_pct: float | None
    residual_loss_pct: float | None
    decomposition_status: str  # complete or partial
    unknown_reasons: tuple[str, ...]

def decompose_roundtrip(*, input_lamports: int, output_lamports: int,
                        buy_quote: dict[str, Any] | None = None,
                        sell_quote: dict[str, Any] | None = None,
                        buy_sim: dict[str, Any] | None = None,
                        sell_sim: dict[str, Any] | None = None) -> CostBreakdown:
    if not isinstance(input_lamports, int) or input_lamports <= 0 or not isinstance(output_lamports, int) or output_lamports < 0:
        raise ValueError("invalid round-trip amounts")
    aggregate = round(max(0.0, min(100.0, (1 - output_lamports / input_lamports) * 100)), 6)
    unknown: list[str] = []
    priority = None
    for obj in (buy_quote, sell_quote, buy_sim, sell_sim):
        if isinstance(obj, dict) and isinstance(obj.get("prioritizationFeeLamports"), int):
            priority = (priority or 0) + obj["prioritizationFeeLamports"]
    swap_fee = None
    for obj in (buy_quote, sell_quote):
        if isinstance(obj, dict) and isinstance(obj.get("feeLamports"), int):
            swap_fee = (swap_fee or 0) + obj["feeLamports"]
    price_impact = None
    impacts: list[float] = []
    for obj in (buy_quote, sell_quote):
        if isinstance(obj, dict):
            raw_impact = obj.get("priceImpactPct")
            if isinstance(raw_impact, (int, float)):
                impacts.append(float(raw_impact))
    if impacts:
        price_impact = round(sum(impacts), 6)
    transfer = None
    unknown.append("transfer_fee_not_authoritatively_observed")
    if swap_fee is None: unknown.append("swap_fee_not_reported")
    if priority is None: unknown.append("priority_fee_not_reported")
    if price_impact is None: unknown.append("price_impact_not_reported")
    unknown.append("residual_route_slippage_not_separable")
    return CostBreakdown(aggregate, swap_fee, priority, transfer, price_impact, None,
                         "complete" if not unknown else "partial", tuple(unknown))
