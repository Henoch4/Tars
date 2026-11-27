"""Separate GTA event position book with explicit entry/exit acknowledgments.

This module owns only GTA event state. It evaluates risk and exit decisions but
never signs or broadcasts a transaction; a future executor must acknowledge a
confirmed fill before the book mutates position state.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .gta_event import GtaEventPolicy, evaluate_exit
from .gta_risk import GtaRiskController


@dataclass
class GtaPosition:
    mint: str
    symbol: str
    entry_price: float
    size_usd: float
    entry_ts: float
    peak_price: float
    ladder_fired: list[bool] = field(default_factory=list)
    token_amount_raw: int | None = None


class GtaBook:
    """Risk-gated GTA positions; execution remains an external adapter."""

    def __init__(self, policy: GtaEventPolicy, state_path: str | Path | None = None):
        self.policy = policy
        self.risk = GtaRiskController.create(policy)
        self.state_path = Path(state_path) if state_path else None
        self.positions: dict[str, GtaPosition] = {}
        self._load()

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            for row in data.get("positions", []):
                pos = GtaPosition(**row)
                self.positions[pos.mint] = pos
            self.risk.spent_usd = float(data.get("spent_usd", 0.0))
        except (OSError, ValueError, TypeError, KeyError):
            # Corrupt state must not create positions or buying power.
            self.positions = {}
            self.risk.spent_usd = 0.0

    def _save(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "positions": [asdict(p) for p in self.positions.values()],
            "spent_usd": self.risk.spent_usd,
            "updated_at": time.time(),
        }
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def authorize_entry(self, candidate: dict[str, Any], now_ts: float | None = None) -> tuple[bool, str]:
        """Run policy + breaker checks without creating a position."""
        now = time.time() if now_ts is None else float(now_ts)
        mint = candidate.get("mint")
        if not isinstance(mint, str) or not mint.strip():
            return False, "missing mint"
        if mint in self.positions:
            return False, "position already open"
        size = candidate.get("size_usd")
        return self.risk.entry_allowed(size, len(self.positions), now)

    def record_entry(self, candidate: dict[str, Any], now_ts: float | None = None) -> GtaPosition:
        """Record an entry only after the external adapter confirms a fill."""
        now = time.time() if now_ts is None else float(now_ts)
        allowed, reason = self.authorize_entry(candidate, now)
        if not allowed:
            raise ValueError(f"GTA entry rejected: {reason}")
        mint = str(candidate["mint"])
        price = float(candidate["entry_price"])
        size = float(candidate["size_usd"])
        if price <= 0 or size <= 0:
            raise ValueError("entry price and size must be positive")
        pos = GtaPosition(
            mint=mint,
            symbol=str(candidate.get("symbol") or mint[:8]),
            entry_price=price,
            size_usd=size,
            entry_ts=now,
            peak_price=price,
            ladder_fired=[False] * len(self.policy.take_profit_ladder),
            token_amount_raw=(
                int(candidate["token_amount_raw"])
                if candidate.get("token_amount_raw") is not None else None
            ),
        )
        self.positions[mint] = pos
        self.risk.record_entry(size)
        self._save()
        return pos

    def exit_decision(self, mint: str, current_price: float, now=None):
        """Evaluate an exit; does not mutate position until acknowledged."""
        pos = self.positions.get(mint)
        if pos is None:
            raise KeyError(f"unknown GTA position {mint}")
        decision = evaluate_exit(
            self.policy,
            entry_price=pos.entry_price,
            peak_price=pos.peak_price,
            entry_ts=pos.entry_ts,
            current_price=float(current_price),
            ladder_fired=tuple(pos.ladder_fired),
            now=now,
        )
        if current_price > pos.peak_price:
            pos.peak_price = float(current_price)
            self._save()
        return decision

    def acknowledge_exit(self, mint: str, net_usd: float, now_ts: float | None = None) -> None:
        """Close a position only after a confirmed external sell fill."""
        pos = self.positions.pop(mint, None)
        if pos is None:
            raise KeyError(f"unknown GTA position {mint}")
        self.risk.record_exit(float(net_usd), time.time() if now_ts is None else float(now_ts))
        self._save()

    def acknowledge_partial_exit(
        self,
        mint: str,
        *,
        remaining_raw: int,
        net_usd: float,
        now_ts: float | None = None,
    ) -> None:
        """Apply a confirmed tranche fill using the post-fill chain balance."""
        pos = self.positions.get(mint)
        if pos is None:
            raise KeyError(f"unknown GTA position {mint}")
        if isinstance(remaining_raw, bool) or not isinstance(remaining_raw, int) or remaining_raw < 0:
            raise ValueError("remaining token amount must be a non-negative integer")
        before = pos.token_amount_raw
        if before is None or remaining_raw >= before:
            raise ValueError("post-fill balance did not reduce the position")
        sold_raw = before - remaining_raw
        ratio = sold_raw / before if before else 1.0
        pos.token_amount_raw = remaining_raw
        pos.size_usd = max(0.0, pos.size_usd * (1.0 - ratio))
        for idx, fired in enumerate(pos.ladder_fired):
            if not fired:
                pos.ladder_fired[idx] = True
                break
        self.risk.record_exit(float(net_usd), time.time() if now_ts is None else float(now_ts))
        self._save()

    def status(self) -> dict[str, Any]:
        return {
            "open_positions": len(self.positions),
            "spent_usd": self.risk.spent_usd,
            "breaker_halted": self.risk.breakers.halted,
            "positions": {mint: asdict(pos) for mint, pos in self.positions.items()},
        }
