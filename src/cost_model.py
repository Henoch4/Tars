"""Small, explicit cost model shared by paper-trading validation tools."""
from dataclasses import dataclass

@dataclass(frozen=True)
class CostModel:
    taker_fee_bps_spot: float = 5.0
    taker_fee_bps_perp: float = 5.0
    slippage_bps_per_leg: float = 3.0
    hold_periods: float = 21.0
    ev_margin: float = 2.0

BOOK_1_COST_MODEL = CostModel()

def break_even_funding_rate(model: CostModel) -> float:
    """Minimum one-period carry covering both trading fees and slippage."""
    return (2.0 * (model.taker_fee_bps_spot + model.taker_fee_bps_perp + 2.0 * model.slippage_bps_per_leg) / model.hold_periods * model.ev_margin) / 10_000
