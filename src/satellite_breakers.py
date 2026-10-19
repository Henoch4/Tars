"""Satellite circuit breakers — pure, deterministic, UNWIRED.

Ports the proven portfolio-level gates from the EVM book (src/execution/
risk_gate.py: max_daily_loss_usd, trailing loss streak, drawdown multiplier)
to the Solana satellite. This module is PURE: no IO, no clock of its own
(callers pass `ts`), no network, no keys. It decides; it never trades.

NOT WIRED into SatelliteLoop try_enter yet — deliberately. Wiring lands after
G1 (one structural change at a time), behind a default-off flag. Keeping the
logic standalone keeps the paper measurement frozen.

Breaker layers (all fail-closed on bad input):
  1. daily loss cap        -> pause new entries until the next UTC day
  2. consecutive-loss streak -> pause new entries for a cooldown window
  3. max open positions     -> refuse the entry that would exceed the cap
  4. portfolio heat         -> refuse while open exposure is over budget
  5. trailing drawdown      -> HARD halt (needs explicit reset) past mult*cap
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

DAY_S = 86400.0


@dataclass
class BreakerLimits:
    max_daily_loss_usd: float = 50.0
    max_consecutive_losses: int = 6
    streak_cooldown_s: float = 6 * 3600.0
    max_open_positions: int = 3
    max_portfolio_heat_usd: float = 75.0
    drawdown_loss_mult: float = 2.0


def day_key(ts: float) -> int:
    """UTC day bucket for a timestamp (daily counters reset on rollover)."""
    return int(float(ts) // DAY_S)


@dataclass
class SatelliteBreakers:
    """Portfolio circuit breakers for the satellite (pure in-memory state)."""

    limits: BreakerLimits = field(default_factory=BreakerLimits)
    daily_loss: dict = field(default_factory=dict)
    consecutive_losses: int = 0
    paused_until: float = 0.0
    halted: bool = False
    halt_reason: str = ""

    # -- inputs --------------------------------------------------------

    def record_result(self, net_usd: float, ts: float) -> None:
        """Feed one completed round-trip's net. Negative -> loss accounting;
        non-negative -> streak reset. Garbage is ignored (fail-closed: never
        invent a loss OR a gain from bad input)."""
        if isinstance(net_usd, bool) or not isinstance(net_usd, (int, float)):
            return
        if not math.isfinite(float(net_usd)):
            return
        if net_usd >= 0:
            self.consecutive_losses = 0
            return
        loss = -float(net_usd)
        k = day_key(ts)
        self.daily_loss[k] = self.daily_loss.get(k, 0.0) + loss
        self.consecutive_losses += 1
        if self.consecutive_losses >= self.limits.max_consecutive_losses:
            self.paused_until = max(self.paused_until, float(ts) + self.limits.streak_cooldown_s)
        day_loss = self.daily_loss.get(k, 0.0)
        if day_loss >= self.limits.max_daily_loss_usd:
            self.paused_until = max(self.paused_until, _end_of_day(ts))
        if day_loss >= self.limits.drawdown_loss_mult * self.limits.max_daily_loss_usd:
            self.halted = True
            self.halt_reason = (f"daily loss ${day_loss:.2f} >= "
                                f"{self.limits.drawdown_loss_mult:.1f}x "
                                f"${self.limits.max_daily_loss_usd:.2f} cap")

    def new_entries_allowed(self, open_count: int, open_exposure_usd: float,
                            ts: float) -> tuple:
        """(allowed, reason). Checks hard halt, pause window, open-count cap,
        and portfolio heat — in that order."""
        if self.halted:
            return False, f"halted: {self.halt_reason}"
        if float(ts) < self.paused_until:
            return False, (f"paused {self.paused_until - float(ts):.0f}s "
                           f"(streak={self.consecutive_losses}, "
                           f"daily=${self.daily_loss.get(day_key(ts), 0.0):.2f})")
        if isinstance(open_count, bool) or not isinstance(open_count, int):
            return False, "open_count unreadable"
        if open_count >= self.limits.max_open_positions:
            return False, f"max open positions ({self.limits.max_open_positions})"
        if isinstance(open_exposure_usd, bool) or not isinstance(open_exposure_usd, (int, float)):
            return False, "open exposure unreadable"
        if float(open_exposure_usd) >= self.limits.max_portfolio_heat_usd:
            return False, (f"portfolio heat ${open_exposure_usd:.2f} >= "
                           f"${self.limits.max_portfolio_heat_usd:.2f}")
        return True, ""

    def reset(self) -> None:
        """Operator reset (clears halt + pause; keeps counters for audit)."""
        self.halted = False
        self.halt_reason = ""
        self.paused_until = 0.0

    # -- persistence ---------------------------------------------------

    def state(self) -> dict:
        return {
            "daily_loss": {str(k): v for k, v in self.daily_loss.items()},
            "consecutive_losses": self.consecutive_losses,
            "paused_until": self.paused_until,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
        }

    @classmethod
    def from_state(cls, state: dict, limits: BreakerLimits | None = None) -> "SatelliteBreakers":
        b = cls(limits=limits or BreakerLimits())
        if not isinstance(state, dict):
            return b
        try:
            b.daily_loss = {int(k): float(v) for k, v in (state.get("daily_loss") or {}).items()}
        except (TypeError, ValueError):
            b.daily_loss = {}
        try:
            b.consecutive_losses = int(state.get("consecutive_losses", 0))
        except (TypeError, ValueError):
            b.consecutive_losses = 0
        try:
            b.paused_until = float(state.get("paused_until", 0.0))
        except (TypeError, ValueError):
            b.paused_until = 0.0
        b.halted = bool(state.get("halted", False))
        b.halt_reason = str(state.get("halt_reason", ""))
        return b


def _end_of_day(ts: float) -> float:
    return (day_key(ts) + 1) * DAY_S
