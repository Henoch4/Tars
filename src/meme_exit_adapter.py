"""Chain-driven exit and flatten adapter for the main satellite book."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable


@dataclass(frozen=True)
class ExitResult:
    mint: str
    attempted: bool
    closed: bool
    remaining_raw: int | None
    reason: str
    receipt: Any = None


class MainSatelliteExitAdapter:
    """Size exits from the latest chain balance and preserve residual dust."""

    def __init__(self, position_loader: Callable[[], Any | None],
                 position_saver: Callable[[Any], None],
                 balance_reader: Callable[[str, str], Any] | None,
                 owner: str,
                 sell_fn: Callable[..., Any]):
        self.position_loader = position_loader
        self.position_saver = position_saver
        self.balance_reader = balance_reader
        self.owner = owner
        self.sell_fn = sell_fn

    def _balance(self, position: Any) -> tuple[int, int]:
        if self.balance_reader is None:
            raise RuntimeError("balance reader unavailable")
        observed = self.balance_reader(position.mint, self.owner)
        raw = int(getattr(observed, "amount_raw", observed))
        decimals = int(getattr(observed, "decimals", position.decimals))
        if raw < 0 or decimals != int(position.decimals):
            raise RuntimeError("chain balance malformed")
        return raw, decimals

    def exit(self, *, confirm: str = "", slippage_bps: int = 100) -> ExitResult:
        position = self.position_loader()
        if position is None:
            return ExitResult("", False, True, 0, "no open position")
        mint = str(position.mint)
        try:
            raw, decimals = self._balance(position)
        except Exception as exc:  # noqa: BLE001
            return ExitResult(mint, False, False, None, str(exc))
        if raw == 0:
            position.token_amount = 0.0
            position.sell_executed = True
            self.position_saver(position)
            return ExitResult(mint, False, True, 0, "already flat")
        if not confirm.strip():
            return ExitResult(mint, False, False, raw, "live confirmation required")
        try:
            receipt = self.sell_fn(mint=mint, amount_raw=raw,
                                   confirm=confirm, slippage_bps=slippage_bps)
        except Exception as exc:  # noqa: BLE001
            return ExitResult(mint, True, False, raw, f"sell failed: {type(exc).__name__}")
        try:
            remaining, _ = self._balance(position)
        except Exception as exc:  # noqa: BLE001
            return ExitResult(mint, True, False, None, f"post-fill read failed: {type(exc).__name__}", receipt)
        position.token_amount = float(Decimal(remaining) / (Decimal(10) ** decimals))
        position.sell_executed = remaining == 0
        self.position_saver(position)
        return ExitResult(mint, True, remaining == 0, remaining,
                          "closed" if remaining == 0 else "residual balance", receipt)

    def flatten(self, *, confirm: str, slippage_bps: int = 800) -> dict:
        result = self.exit(confirm=confirm, slippage_bps=slippage_bps)
        return {"attempted": result.attempted, "closed": result.closed,
                "unresolved": [] if result.closed else ([result.mint] if result.mint else []),
                "results": [result.__dict__]}
