"""GTA event risk controller: budgets + circuit breakers, no execution."""
from __future__ import annotations

from dataclasses import dataclass
from .gta_event import GtaEventPolicy
from .satellite_breakers import BreakerLimits, SatelliteBreakers


@dataclass
class GtaRiskController:
    policy: GtaEventPolicy
    breakers: SatelliteBreakers
    spent_usd: float = 0.0

    @classmethod
    def create(cls, policy: GtaEventPolicy) -> "GtaRiskController":
        return cls(
            policy=policy,
            breakers=SatelliteBreakers(
                BreakerLimits(
                    max_daily_loss_usd=policy.max_total_usd,
                    max_consecutive_losses=3,
                    streak_cooldown_s=6 * 3600,
                    max_open_positions=policy.max_open_positions,
                    max_portfolio_heat_usd=policy.max_total_usd,
                    drawdown_loss_mult=1.0,
                )
            ),
        )

    def entry_allowed(self, size_usd: float, open_count: int, now_ts: float) -> tuple[bool, str]:
        """Apply event arm, budget, and breaker gates before an entry."""
        armed, reason = self.policy.broadcast_allowed()
        if not armed:
            return False, reason
        try:
            size = float(size_usd)
        except (TypeError, ValueError):
            return False, "entry size unreadable"
        if size <= 0 or size > self.policy.max_per_token_usd:
            return False, "per-token budget exceeded"
        if self.spent_usd + size > self.policy.max_total_usd:
            return False, "event budget exceeded"
        allowed, breaker_reason = self.breakers.new_entries_allowed(
            open_count, self.spent_usd, now_ts
        )
        if not allowed:
            return False, breaker_reason
        return True, "entry risk checks passed"

    def record_entry(self, size_usd: float) -> None:
        self.spent_usd += float(size_usd)

    def record_exit(self, net_usd: float, now_ts: float) -> None:
        self.spent_usd = max(0.0, self.spent_usd - max(0.0, float(net_usd)))
        self.breakers.record_result(float(net_usd), now_ts)
