"""Adapters that connect the main satellite book to chain truth.

Only reconciliation is implemented here. Entry, sell, and signing remain
separate injected handlers owned by the supervisor.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable


@dataclass(frozen=True)
class PositionBalance:
    mint: str
    expected_raw: int
    observed_raw: int
    decimals: int


class SinglePositionReconciler:
    """Exact raw-unit reconciler for the current main-satellite book."""

    def __init__(self, position_loader: Callable[[], Any | None],
                 balance_reader: Callable[[str, str], Any], owner: str):
        self.position_loader = position_loader
        self.balance_reader = balance_reader
        self.owner = owner

    def __call__(self) -> dict:
        position = self.position_loader()
        if position is None:
            return {"ok": True, "counts": {"local": 0, "onchain": 0}, "positions": []}
        mint = getattr(position, "mint", None)
        decimals = getattr(position, "decimals", None)
        token_amount = getattr(position, "token_amount", None)
        if not isinstance(mint, str) or not mint.strip():
            return {"ok": False, "reason": "position mint missing"}
        if isinstance(decimals, bool) or not isinstance(decimals, int) or decimals < 0:
            return {"ok": False, "reason": "position decimals invalid", "mint": mint}
        if isinstance(token_amount, bool) or not isinstance(token_amount, (int, float)) or token_amount < 0:
            return {"ok": False, "reason": "position token amount invalid", "mint": mint}
        try:
            exact = Decimal(str(token_amount)) * (Decimal(10) ** decimals)
            if exact != exact.to_integral_value():
                return {"ok": False, "reason": "position amount is not exact raw units", "mint": mint}
            expected_raw = int(exact)
        except (InvalidOperation, ValueError):
            return {"ok": False, "reason": "position token amount invalid", "mint": mint}
        try:
            observed = self.balance_reader(mint, self.owner)
            observed_raw = int(getattr(observed, "amount_raw", observed))
            observed_decimals = int(getattr(observed, "decimals", decimals))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "balance reader failed", "error": type(exc).__name__, "mint": mint}
        if observed_raw < 0 or observed_decimals != decimals:
            return {"ok": False, "reason": "chain balance malformed", "mint": mint}
        row = PositionBalance(mint, expected_raw, observed_raw, decimals)
        return {
            "ok": expected_raw == observed_raw,
            "counts": {"local": 1, "onchain": 1 if observed_raw > 0 else 0},
            "positions": [row.__dict__],
            "reason": "matched" if expected_raw == observed_raw else "raw balance mismatch",
        }
