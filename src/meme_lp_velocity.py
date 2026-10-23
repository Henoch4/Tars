"""LP-velocity gate — measure how fast a pool's liquidity is draining.

Window-3 autopsy found the killer class drains 90-95% of its LP within
5-15 minutes, and every one of those entries passed the structural
screen taken at the pass moment (a single snapshot). The one-change
candidate for restart #5 is therefore a CONFIRMATION STAGE: instead of
entering on the pass, observe the pool one more cycle and enter only if
the liquidity is not visibly draining. This module does the measuring
and the verdict; it deliberately knows nothing about the satellite loop
(no imports of final_state / live scheduling) and holds zero live
state, so it can go behind a feature flip and under the offline replay.

Doctrine wiring (mirrors meme_satellite/ScreenResult):
- Samples: (ts, liquidity_usd) pairs. A single snapshot cannot produce a
  velocity -> "insufficient data" verdict. Never treats missing as zero.
- Strict numeric reads: bool/NaN/inf are unobserved, never zeros.
- Pair span guard: two samples further apart than MAX_SAMPLE_GAP_S say
  nothing about a fast drain; stale pairs are ignored, not averaged in.
- Fail-closed direction: when the gate is ENABLED it may reject; while
  disabled it may only add notes. `armed()` is the feature flip the
  wiring owns.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

MIN_SAMPLES = 2  # velocity needs a before and an after
MAX_SAMPLE_GAP_S = 900.0  # samples further apart are not one episode (15 min)
MAX_VELOCITY_CLAMP_PCT_PER_MIN = 500.0  # sanity clamp; beyond this the readings are broken
MIN_SPAN_S = 5.0  # pairs closer than this multiply float noise into wild rates

# Default reject threshold: drain observed on the killer class runs
# 17-19%/min; the confirmation question is "is it falling at all, fast
# enough to notice within one cycle?" Calibrated in the offline replay
# before any arm flips; the number here is the starting bar, not gospel.
DEFAULT_DROP_PCT_PER_MIN = 4.0


@dataclass
class LPSample:
    ts: float
    liquidity_usd: float


@dataclass
class VelocityVerdict:
    """What the confirmation stage says about a candidate's LP.

    action is 'reject' | 'note' | 'pass' | 'insufficient'. With
    armed=False the gate may never return 'reject' — notes only."""

    mint: str
    action: str
    reason: str
    drop_pct_per_min: float | None = None  # worst concurrent-pair drain rate
    overall_drop_pct_per_min: float | None = None  # first -> last drain rate
    sample_count: int = 0
    span_s: float = 0.0
    notes: list = field(default_factory=list)


def _finite_nonbool(value, name: str, notes: list) -> float | None:
    """Bool/NaN/inf/non-number are unobserved data, never zeros."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        notes.append(f"{name} unobserved (type {type(value).__name__})")
        return None
    if not math.isfinite(float(value)):
        notes.append(f"{name} non-finite")
        return None
    return float(value)


def pair_drop_pct_per_min(liquidity_a: float, liquidity_b: float,
                          ts_a: float, ts_b: float,
                          notes: list) -> float | None:
    """Signed change rate, %/min: positive = rising, negative = falling.
    None = pair unusable (unmeasurable, never zero-by-missing-data)."""
    if ts_b <= ts_a:
        notes.append("sample timestamps out of order")
        return None
    span = ts_b - ts_a
    if span < MIN_SPAN_S:
        notes.append(f"pair too close to read ({span:.0f}s)")
        return None
    if liquidity_a <= 0.0:
        notes.append("reference sample has zero/negative liquidity")
        return None
    change = (liquidity_b - liquidity_a) / liquidity_a * 100.0
    rate = abs(change) / span * 60.0
    if not math.isfinite(rate) or rate > MAX_VELOCITY_CLAMP_PCT_PER_MIN:
        notes.append(f"velocity beyond sanity clamp ({rate:,.0f}%/min)")
        return None
    return change / span * 60.0


def liquidity_verdict(mint: str, samples: list,
                      threshold_pct_per_min: float = DEFAULT_DROP_PCT_PER_MIN,
                      armed: bool = False) -> VelocityVerdict:
    """Confirmation-stage verdict on as-observed LP samples.

    Metrics are ALWAYS computed when the data allows (the replay needs
    them). The gate only rejects when armed=True AND the worst measured
    drain rate crosses the threshold. Unarmed = note/pass only."""
    notes: list = []
    parsed: list = []
    for s in samples:
        pair = s if isinstance(s, tuple) else (s.ts, s.liquidity_usd)
        ts = _finite_nonbool(pair[0], "sample ts", notes)
        liq = _finite_nonbool(pair[1], "sample liquidity", notes)
        if ts is None or liq is None:
            continue
        parsed.append((ts, liq))
    if len(parsed) < MIN_SAMPLES:
        notes.append(f"only {len(parsed)} usable sample(s) "
                     f"(< {MIN_SAMPLES}): velocity unmeasurable")
        return VelocityVerdict(mint=mint, action="insufficient",
                               reason="insufficient data", sample_count=len(parsed),
                               notes=notes)
    parsed.sort(key=lambda p: p[0])
    span_s = parsed[-1][0] - parsed[0][0]

    worst = None  # most negative rate seen in any valid pair
    pair_notes = []
    for a, b in zip(parsed, parsed[1:]):
        if b[0] - a[0] > MAX_SAMPLE_GAP_S:
            pair_notes.append(f"gap {b[0] - a[0]:.0f}s exceeds episode window; pair skipped")
            continue
        rate = pair_drop_pct_per_min(a[1], b[1], a[0], b[0], pair_notes)
        if rate is None:
            continue
        if worst is None or rate < worst:
            worst = rate
    if worst is None:
        reason = "; ".join(pair_notes[:2]) if pair_notes else "no readable pair"
        notes.append(f"velocity unmeasurable: {reason}")
        return VelocityVerdict(mint=mint, action="insufficient",
                               reason="insufficient data", sample_count=len(parsed),
                               span_s=span_s, notes=notes)

    overall = pair_drop_pct_per_min(parsed[0][1], parsed[-1][1],
                                    parsed[0][0], parsed[-1][0], notes)
    v = VelocityVerdict(mint=mint, action="note", reason="LP velocity measured",
                        drop_pct_per_min=worst, overall_drop_pct_per_min=overall,
                        sample_count=len(parsed), span_s=span_s, notes=notes)
    if worst < -threshold_pct_per_min:
        note = (f"liquidity draining {abs(worst):.1f}%/min "
                f"(> {threshold_pct_per_min:.0f}%/min briefing)")
        v.notes.append(note)
        v.reason = note
        if armed:
            v.action = "reject"
            return v
    elif worst < 0:
        v.notes.append(f"liquidity falling but slow ({abs(worst):.1f}%/min): shaped in")
        v.reason = f"liquidity falling but slow ({abs(worst):.1f}%/min)"
        v.action = "pass"
    else:
        v.notes.append("liquidity not falling: confirmation passed")
        v.reason = "liquidity not falling: confirmation passed"
        v.action = "pass"
    return v
