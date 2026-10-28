"""Cost model for delta-neutral carry (Book 1).

Single source of truth for fee/slippage/funding cost assumptions used by
the backtester, the funding-arb gate, and the safety net. All values are in
basis points (bps = 1/100 of 1%).

Default values reflect X Layer perp trading assumptions:
- Spot taker fee: 5 bps per side
- Perp taker fee: 5 bps per side
- Slippage: 3 bps per leg per side
- Hold: 21 funding periods (= 7 days at 8h cadence)
- EV margin: 2.0x (carry must clear 2x round-trip cost)

Round-trip cost on notional:
  per leg per side = fee + slippage
  two legs (spot + perp), two sides (entry + exit)
  total_cost_bps = 2 * (fee_spot + fee_perp + 2 * slippage)
  With defaults: 2 * (5 + 5 + 6) = 32 bps

Per-period break-even funding rate:
  per_period_be = total_cost_bps / 1e4 * CARRY_EV_MARGIN / hold_periods
  With defaults: 32 / 10000 * 2.0 / 21 ≈ 0.000305 (0.0305% per 8h period)
"""
from __future__ import annotations

from dataclasses import dataclass

CARRY_EV_MARGIN: float = 2.0


@dataclass(frozen=True)
class CostModel:
    """Cost parameters for delta-neutral carry.

    All fee/slippage values are in basis points (bps).
    hold_periods is the number of funding periods (8h each) the position
    is expected to be held.
    """

    taker_fee_bps_spot: float = 5.0
    taker_fee_bps_perp: float = 5.0
    slippage_bps_per_leg: float = 3.0
    hold_periods: float = 21.0
    ev_margin: float = CARRY_EV_MARGIN

    def __post_init__(self) -> None:
        if self.taker_fee_bps_spot < 0:
            raise ValueError(f"taker_fee_bps_spot must be non-negative, got {self.taker_fee_bps_spot}")
        if self.taker_fee_bps_perp < 0:
            raise ValueError(f"taker_fee_bps_perp must be non-negative, got {self.taker_fee_bps_perp}")
        if self.slippage_bps_per_leg < 0:
            raise ValueError(f"slippage_bps_per_leg must be non-negative, got {self.slippage_bps_per_leg}")
        if self.hold_periods <= 0:
            raise ValueError(f"hold_periods must be positive, got {self.hold_periods}")
        if self.ev_margin <= 0:
            raise ValueError(f"ev_margin must be positive, got {self.ev_margin}")


def round_trip_cost_bps(cost_model: CostModel) -> float:
    """Total round-trip cost in basis points of notional.

    Both legs (spot + perp), both sides (entry + exit):
      per leg per side = fee + slippage
      total = 2 * (fee_spot + fee_perp + 2 * slippage)
    """
    return 2.0 * (
        cost_model.taker_fee_bps_spot
        + cost_model.taker_fee_bps_perp
        + 2.0 * cost_model.slippage_bps_per_leg
    )


def per_period_cost_bps(cost_model: CostModel) -> float:
    """Amortized per-period cost in basis points.

    Spreads the round-trip cost over the hold period.
    """
    return round_trip_cost_bps(cost_model) / cost_model.hold_periods


def break_even_funding_rate(
    cost_model: CostModel,
    survival_prob: float = 1.0,
    pin_state: int = 0,
) -> float:
    """Minimum per-period funding rate for carry to clear costs.

    Delegates to :func:`src.signals.carry_break_even_rate` with the cost
    model's parameters, scaled by ``ev_margin`` (so carry must clear 2x cost).
    Imported lazily to avoid a circular import with ``src.signals``.
    """
    from .signals import carry_break_even_rate

    return carry_break_even_rate(
        taker_fee_bps_spot=cost_model.taker_fee_bps_spot,
        taker_fee_bps_perp=cost_model.taker_fee_bps_perp,
        slippage_bps_per_leg=cost_model.slippage_bps_per_leg,
        hold_periods=cost_model.hold_periods,
        survival_prob=survival_prob,
        pin_state=pin_state,
        ev_margin=cost_model.ev_margin,
    )


# Canonical Book 1 cost assumptions.
#
# These are the values used by the autonomous trading agent for funding-arb
# gating and by the backtester for net-of-costs evaluation.
BOOK_1_COST_MODEL = CostModel(
    taker_fee_bps_spot=5.0,
    taker_fee_bps_perp=5.0,
    slippage_bps_per_leg=3.0,
    hold_periods=21.0,
    ev_margin=CARRY_EV_MARGIN,
)

__all__ = [
    "CostModel",
    "round_trip_cost_bps",
    "per_period_cost_bps",
    "break_even_funding_rate",
    "CARRY_EV_MARGIN",
    "BOOK_1_COST_MODEL",
]
