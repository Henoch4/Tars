"""Venue-aware execution costs for the satellite (ports Stockulus §8.3).

Ported from docs/solana/dbc_configs.sol.json + src/solana/regime_hmm.py
(Stockulus track): Meteora DBC fee schedules per regime, selected by the
HMM state, with AMM price impact as size/liquidity — not flat bps.

- calm: linear 120bps -> 100bps over 60 periods, regimeScale 1.0
  (config 3WDNBkpE67v2wzMWY1mgyyAuE4eugFBbgniRa1tqojFY, devnet)
- stress: exponential 900bps -> 100bps over 60 periods, regimeScale 0.7
  (config 7rhZYfkUHcbHYkkv2FBGqE8KnQC8f66WsGM8fBuv7P2W, devnet)

Config addresses are provenance (which schedule was measured), not
authority — the math below is what the satellite consumes. Regime 1
(mid/trend) uses the calm schedule at 1.2x (dbc_action_for_regime).
Mainnet schedules/addresses replace these when the satellite leaves
paper; the selection + impact math does not change.
"""
from __future__ import annotations

import math

from .solana.regime_hmm import infer_regime_simple

SCHEDULES = {
    "calm": {"fee_start_bps": 120.0, "fee_end_bps": 100.0, "periods": 60,
             "curve": "linear", "regime_scale": 1.0,
             "config": "3WDNBkpE67v2wzMWY1mgyyAuE4eugFBbgniRa1tqojFY"},
    "stress": {"fee_start_bps": 900.0, "fee_end_bps": 100.0, "periods": 60,
               "curve": "exponential", "regime_scale": 0.7,
               "config": "7rhZYfkUHcbHYkkv2FBGqE8KnQC8f66WsGM8fBuv7P2W"},
}
STATE_SCHEDULE = {0: ("calm", 1.0), 1: ("calm", 1.2), 2: ("stress", 2.0)}


def _num(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return float(value)


def hmm_state_for_market(vol: float, funding_z: float, basis: float,
                         ret: float) -> int:
    """HMM regime state 0/1/2 with strict inputs (garbage raises instead
    of silently thresholding). Thresholds are Stockulus-tuned (tamer than
    crypto): state 2 trips easier than a crypto-native tuning would."""
    return infer_regime_simple(_num(vol, "vol"), _num(funding_z, "funding_z"),
                               _num(basis, "basis"), _num(ret, "ret"))


def select_fee_schedule(state: int) -> dict:
    """Regime state -> (schedule, fee multiplier). Unknown states raise:
    an unmapped regime must never silently clear as calm."""
    try:
        name, mult = STATE_SCHEDULE[state]
    except (KeyError, TypeError):
        raise ValueError(f"unknown HMM state {state!r} (want 0, 1, or 2)") from None
    sched = dict(SCHEDULES[name])
    sched["fee_mult"] = mult
    return sched


def meteora_leg_cost_bps(size_usd: float, liquidity_usd: float,
                         schedule: dict) -> float:
    """Round-trip cost estimate for one Meteora leg in bps: schedule fee
    (start-of-curve × regime multiplier, conservative: worst price first)
    plus AMM impact size/liquidity × 10000. Zero/negative liquidity is
    unpriceable — raises instead of returning a finite number."""
    size = _num(size_usd, "size_usd")
    liq = _num(liquidity_usd, "liquidity_usd")
    if size < 0:
        raise ValueError(f"size_usd must be >= 0, got {size_usd!r}")
    if liq <= 0:
        raise ValueError("liquidity_usd must be positive (unpriceable otherwise)")
    fee = float(schedule["fee_start_bps"]) * float(schedule.get("fee_mult", 1.0))
    impact_bps = size / liq * 10000.0
    return fee + impact_bps


def satellite_round_trip_cost_usd(size_usd: float, liquidity_usd: float,
                                  hmm_state: int) -> dict:
    """Full paper cost for a satellite round trip at satellite size:
    schedule + mult selected by regime, impact by size/liquidity, both
    legs (in + out). Returns the breakdown, not just a number."""
    sched = select_fee_schedule(hmm_state)
    per_leg_bps = meteora_leg_cost_bps(size_usd, liquidity_usd, sched)
    total_usd = size_usd * per_leg_bps / 10000.0 * 2
    return {
        "schedule": "calm" if sched["curve"] == "linear" else "stress",
        "fee_mult": sched["fee_mult"],
        "per_leg_bps": round(per_leg_bps, 2),
        "round_trip_usd": round(total_usd, 4),
    }
