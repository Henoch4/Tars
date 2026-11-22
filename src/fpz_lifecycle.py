"""Actual-quantity FPZ lifecycle accounting for paper/reconciliation paths."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FpzLifecycleLedger:
    requested_notional: float
    filled_notional: float = 0.0
    exit_notional: float = 0.0
    settled_funding: float = 0.0
    fees: float = 0.0

    def __post_init__(self) -> None:
        if self.requested_notional <= 0:
            raise ValueError("requested notional must be positive")
        for value in (self.filled_notional, self.exit_notional,
                      self.settled_funding, self.fees):
            if value < 0:
                raise ValueError("lifecycle amounts cannot be negative")
        if self.filled_notional > self.requested_notional:
            raise ValueError("filled notional exceeds requested amount")
        if self.exit_notional > self.filled_notional:
            raise ValueError("exit exceeds filled amount")

    @property
    def remaining_notional(self) -> float:
        return self.filled_notional - self.exit_notional

    @property
    def realized_pnl(self) -> float:
        return self.exit_notional - self.filled_notional + self.settled_funding - self.fees

    @property
    def closed(self) -> bool:
        return self.filled_notional > 0 and self.remaining_notional == 0

    def record_fill(self, amount: float) -> "FpzLifecycleLedger":
        if amount < 0 or self.filled_notional + amount > self.requested_notional:
            raise ValueError("fill exceeds requested notional")
        return FpzLifecycleLedger(self.requested_notional,
                                  self.filled_notional + amount,
                                  self.exit_notional, self.settled_funding,
                                  self.fees)

    def record_exit(self, amount: float) -> "FpzLifecycleLedger":
        if amount < 0 or self.exit_notional + amount > self.filled_notional:
            raise ValueError("exit exceeds filled notional")
        return FpzLifecycleLedger(self.requested_notional,
                                  self.filled_notional,
                                  self.exit_notional + amount,
                                  self.settled_funding, self.fees)

    def record_funding(self, amount: float) -> "FpzLifecycleLedger":
        if amount < 0:
            raise ValueError("settled funding cannot be negative")
        return FpzLifecycleLedger(self.requested_notional,
                                  self.filled_notional, self.exit_notional,
                                  self.settled_funding + amount, self.fees)

    def record_fee(self, amount: float) -> "FpzLifecycleLedger":
        if amount < 0:
            raise ValueError("fees cannot be negative")
        return FpzLifecycleLedger(self.requested_notional,
                                  self.filled_notional, self.exit_notional,
                                  self.settled_funding, self.fees + amount)
