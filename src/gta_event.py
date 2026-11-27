"""Guarded policy for the GTA VI Solana event satellite.

This module is deliberately independent from the existing paper satellite and
does not discover tokens, hold keys, or submit swaps.  It evaluates a fully
enriched candidate and returns a deterministic allow/skip decision.  The
policy is disabled by default; a future live runner must enforce this policy
and a separate broadcast confirmation before touching a wallet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import re
import time
from typing import Any, Literal


DEFAULT_KEYWORDS = (
    "gta", "gta6", "gta vi", "grand theft", "dacity", "audacity",
    "vice city", "leonida", "lucia", "jason", "wasted",
)


@dataclass(frozen=True)
class GtaEventPolicy:
    """Closed-form event limits; all money values are USD notionals."""

    enabled: bool = False
    live_enabled: bool = False
    start_utc: datetime | None = None
    end_utc: datetime | None = None
    max_total_usd: float = 0.0
    max_per_token_usd: float = 0.0
    max_open_positions: int = 0
    max_top10_pct: float = 20.0
    min_liquidity_multiple: float = 10.0
    min_holders: int = 100
    keywords: tuple[str, ...] = DEFAULT_KEYWORDS
    stop_loss_pct: float = 35.0
    take_profit_ladder: tuple[tuple[float, float], ...] = ((45.0, 100.0), (30.0, 200.0))
    runner_trailing_stop_pct: float = 25.0
    max_hold_hours: float = 96.0
    force_flat_utc: datetime | None = None

    def __post_init__(self) -> None:
        if self.max_total_usd < 0 or self.max_per_token_usd < 0:
            raise ValueError("event budgets cannot be negative")
        if self.max_open_positions < 0:
            raise ValueError("max_open_positions cannot be negative")
        if not (0 < self.max_top10_pct <= 100):
            raise ValueError("max_top10_pct must be in (0, 100]")
        if self.min_liquidity_multiple < 1:
            raise ValueError("min_liquidity_multiple must be >= 1")
        if self.min_holders < 1:
            raise ValueError("min_holders must be positive")
        if not (0 < self.stop_loss_pct < 100):
            raise ValueError("stop_loss_pct must be in (0, 100)")
        if not (0 < self.runner_trailing_stop_pct < 100):
            raise ValueError("runner_trailing_stop_pct must be in (0, 100)")
        if self.max_hold_hours <= 0:
            raise ValueError("max_hold_hours must be positive")
        for dt in (self.start_utc, self.end_utc, self.force_flat_utc):
            if dt is not None and dt.tzinfo is None:
                raise ValueError("event timestamps must be timezone-aware")
        if self.start_utc and self.end_utc and self.end_utc <= self.start_utc:
            raise ValueError("event end must be after start")

    def in_window(self, now: datetime | None = None) -> bool:
        if not self.enabled or self.start_utc is None or self.end_utc is None:
            return False
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return self.start_utc <= current < self.end_utc

    def broadcast_allowed(self, now: datetime | None = None) -> tuple[bool, str]:
        """Independent live arm: both flags, window, and positive budgets."""
        if not self.live_enabled:
            return False, "live policy disabled"
        if not self.enabled:
            return False, "event policy disabled"
        if not self.in_window(now):
            return False, "outside event window"
        if self.max_total_usd <= 0 or self.max_per_token_usd <= 0:
            return False, "budgets are not configured"
        if self.max_open_positions <= 0:
            return False, "open-position cap is not configured"
        return True, "live window and budgets are armed"

    def keyword_match(self, name: str | None, symbol: str | None) -> bool:
        haystack = " ".join(x for x in (name, symbol) if isinstance(x, str)).lower()
        haystack = re.sub(r"[^a-z0-9]+", " ", haystack)
        return any(k.lower() in haystack for k in self.keywords)


@dataclass(frozen=True)
class GtaExitDecision:
    action: Literal["hold", "sell_partial", "sell_all", "halt"]
    sell_pct: float
    reason: str


def evaluate_exit(
    policy: GtaEventPolicy,
    *,
    entry_price: float,
    peak_price: float,
    entry_ts: float,
    current_price: float,
    ladder_fired: tuple[bool, ...] = (),
    now: datetime | None = None,
) -> GtaExitDecision:
    """Pure mechanical exit evaluation for a GTA position."""
    values = [_finite(v) for v in (entry_price, peak_price, entry_ts, current_price)]
    if any(v is None or v <= 0 for v in values):
        return GtaExitDecision("halt", 0.0, "invalid position or price data")
    entry, peak, entry_time, current = values  # type: ignore[misc]
    if current > peak:
        peak = current
    current_dt = now or datetime.now(timezone.utc)
    if policy.force_flat_utc and current_dt.astimezone(timezone.utc) >= policy.force_flat_utc:
        return GtaExitDecision("sell_all", 100.0, "event window force-flat")
    hold_hours = max(0.0, time.time() - entry_time) / 3600.0
    if hold_hours >= policy.max_hold_hours:
        return GtaExitDecision("sell_all", 100.0, f"time exit after {hold_hours:.1f}h")
    peak_drop = (peak - current) / peak * 100.0
    if peak_drop >= policy.stop_loss_pct:
        return GtaExitDecision("sell_all", 100.0, f"stop-loss {peak_drop:.1f}% from peak")
    gain = (current / entry - 1.0) * 100.0
    fired = list(ladder_fired) + [False] * max(0, len(policy.take_profit_ladder) - len(ladder_fired))
    for idx, (sell_pct, gain_pct) in enumerate(policy.take_profit_ladder):
        if not fired[idx] and gain >= gain_pct:
            return GtaExitDecision("sell_partial", float(sell_pct), f"take-profit +{gain:.1f}%")
    if any(fired):
        trail = (peak - current) / peak * 100.0
        if trail >= policy.runner_trailing_stop_pct:
            return GtaExitDecision("sell_all", 100.0, f"runner trail {trail:.1f}% from peak")
    return GtaExitDecision("hold", 0.0, f"hold gain={gain:.1f}% drawdown={peak_drop:.1f}%")


@dataclass(frozen=True)
class GtaDecision:
    allowed: bool
    reason: str
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def evaluate_candidate(
    policy: GtaEventPolicy,
    candidate: dict[str, Any],
    *,
    spent_usd: float = 0.0,
    open_positions: int = 0,
    now: datetime | None = None,
) -> GtaDecision:
    """Fail-closed evaluation of one enriched candidate.

    Missing hard-filter inputs reject the candidate; no missing value is
    interpreted as a safe default.
    """
    reasons: list[str] = []
    if not policy.enabled:
        return GtaDecision(False, "policy disabled", ("GTA_LIVE_ENABLED is false",))
    if not policy.in_window(now):
        return GtaDecision(False, "outside event window", ("event window inactive",))
    if open_positions >= policy.max_open_positions:
        return GtaDecision(False, "open-position cap", ("maximum open positions reached",))

    if not policy.keyword_match(candidate.get("name"), candidate.get("symbol")):
        return GtaDecision(False, "theme mismatch", ("no configured GTA keyword",))

    mint = candidate.get("mint")
    if not isinstance(mint, str) or not mint.strip():
        return GtaDecision(False, "missing mint", ("candidate mint unavailable",))

    size = _finite(candidate.get("size_usd"))
    liquidity = _finite(candidate.get("liquidity_usd"))
    top10 = _finite(candidate.get("top10_hold_pct"))
    holders = candidate.get("holder_count")
    if size is None or size <= 0:
        reasons.append("size_usd missing or non-positive")
    if liquidity is None or liquidity <= 0:
        reasons.append("liquidity_usd missing or non-positive")
    if top10 is None:
        reasons.append("top10_hold_pct missing")
    if isinstance(holders, bool) or not isinstance(holders, int):
        reasons.append("holder_count missing")
    if reasons:
        return GtaDecision(False, "missing hard-filter data", tuple(reasons))

    if candidate.get("mint_authority_disabled") is not True:
        reasons.append("mint authority not confirmed disabled")
    if candidate.get("freeze_authority_disabled") is not True:
        reasons.append("freeze authority not confirmed disabled")
    if candidate.get("honeypot") is not False:
        reasons.append("honeypot/sellability not cleared")
    if candidate.get("transfer_tax_pct") is not None:
        tax = _finite(candidate.get("transfer_tax_pct"))
        if tax is None or tax > 0:
            reasons.append("transfer tax not zero")
    if candidate.get("bundled_buy_pct") is not None:
        bundled = _finite(candidate.get("bundled_buy_pct"))
        if bundled is None or bundled > 25:
            reasons.append("bundle/sniper concentration too high")
    # Token-2022 metadata is surfaced on the candidate (token2022_token_metadata)
    # for audit-trail visibility but is irrelevant to the fee/hook gate: it
    # never enters hard_risk_flags and never blocks a candidate here.
    # Malformed metadata already fails closed in the mint parser.
    if top10 > policy.max_top10_pct:
        reasons.append(f"top10 concentration {top10:.2f}% exceeds {policy.max_top10_pct:.2f}%")
    if holders < policy.min_holders:
        reasons.append(f"holder count {holders} below {policy.min_holders}")
    if liquidity < size * policy.min_liquidity_multiple:
        reasons.append("liquidity is below configured size multiple")
    if size > policy.max_per_token_usd:
        reasons.append("per-token budget exceeded")
    if spent_usd + size > policy.max_total_usd:
        reasons.append("event budget exceeded")
    if reasons:
        return GtaDecision(False, "hard filter rejection", tuple(reasons))
    return GtaDecision(True, "candidate passed GTA event policy", ())
