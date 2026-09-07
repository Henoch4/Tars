"""
Equal SafetyNet — balances risk to secure decent return ratio.

Risk leg: hard 1% of book per trade.
Reward leg: survival-weighted E[PnL] must be >= R * target_rr and q20 >16bps when available.
Wired between signal sizing and RiskGate — sizing filter, not hard wall (RiskGate remains authority).
"""
from __future__ import annotations

from dataclasses import dataclass

from .signals import (
    carry_break_even_rate,
    expected_carry_pnl,
    is_quantile_pass,
)


@dataclass
class SafetyNetResult:
    approved: bool
    reason: str
    size_usd: float | None = None
    stop_pct: float | None = None
    take_pct: float | None = None
    expected_pnl: float | None = None


def safety_net(
    signal,  # Signal
    capital: float,
    target_rr: float = 1.5,
    risk_pct: float = 0.01,
    survival: float = 1.0,
    pin_state: int = 0,
    q20: float | None = None,
    funding_per_period: list[float] | None = None,
    hazards: list[float] | None = None,
    cost_per_period: float | None = None,
) -> SafetyNetResult:
    """
    Equal SafetyNet: risk R = capital * risk_pct, need E[PnL] >= R*target_rr.

    - If q20 provided, gate q20>16bps (Koenker) — else use signal confidence as proxy (already 7200+ for SOL live)
    - If funding/hazards provided, use survival-weighted E[PnL]; else use break-even as cost proxy
    - Returns approved=False -> caller should make NEUTRAL (no Decision)
    """
    if signal.direction == "NEUTRAL" or not signal.is_tradeable:
        return SafetyNetResult(False, "signal not tradeable")

    # Quantile gate — only when ML q20 available; else proxy via confidence (live SOL 7200+)
    if q20 is not None:
        if not is_quantile_pass(q20, 16.0):
            return SafetyNetResult(False, f"q20 {q20:.1f} <16bps")
        # coverage should be ~0.2 OOS — checked in validation, not here
    else:
        if signal.confidence_bps < 7000:
            return SafetyNetResult(False, f"confidence {signal.confidence_bps} <7000 proxy for q20")

    # Risk leg — equal risk
    risk_usd = capital * risk_pct
    # Derive stop from signal vol if available, else 1% hard
    vol = signal.metadata.get("vol", 0.0) if isinstance(signal.metadata, dict) else 0.0
    stop_pct = max(0.005, min(0.02, vol * 1.5)) if vol and vol > 0 else 0.01
    # Size that risks exactly R
    # signal.size_usd not yet computed here — caller passes capital, we compute size cap
    size_cap = risk_usd / stop_pct
    # If signal already has implied size via confidence, respect it but cap
    # For now use risk-based size (will be re-capped by RiskGate max_position_usd)
    size_usd = size_cap

    # Reward leg — survival-weighted
    if funding_per_period is not None and hazards is not None:
        if cost_per_period is None:
            cost_per_period = carry_break_even_rate(survival_prob=survival, pin_state=pin_state)
        try:
            exp = expected_carry_pnl(funding_per_period, hazards, cost_per_period)
        except Exception as e:
            return SafetyNetResult(False, f"E[PnL] calc failed: {e}")
    else:
        # Fallback: use break-even as hurdle, survival scales it
        be = carry_break_even_rate(survival_prob=survival, pin_state=pin_state)
        # Approximate funding as signal's funding_rate * hold 21 periods
        fr = signal.metadata.get("funding_rate", 0.0) if isinstance(signal.metadata, dict) else 0.0
        exp = fr * 21 * survival - be * 21  # rough
        if pin_state != 0:
            # clamped — need quantile feature, be conservative
            exp *= 0.5

    if exp < risk_usd * target_rr:
        return SafetyNetResult(False, f"E[PnL] {exp:.2f} < R*RR {risk_usd*target_rr:.2f}", expected_pnl=exp)

    take_pct = stop_pct * target_rr
    return SafetyNetResult(True, "SafetyNet pass", size_usd=size_usd, stop_pct=stop_pct, take_pct=take_pct, expected_pnl=exp)
