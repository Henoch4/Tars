"""Book 3 — advisory/explainability layer (Sendo "recommend, never order").

NON-TRADING BY CONSTRUCTION. This module produces human-approvable
*explanations* and *recommendations in words*. The deterministic RiskGate
(src/execution/risk_gate.py) remains the ONLY order authority; nothing here
can become an order because nothing here produces order-shaped data.

Structural separation (pinned by tests/test_advisory.py, not just asserted
in this docstring):
  1. This file imports NOTHING from src.execution, src.agent, src.signals,
     or the OKX/executor surfaces. It consumes only primitives the caller
     hands it (strings, floats, ints).
  2. `Advisory` deliberately has NO field an order-lifter would want
     (direction, confidence_bps, size, entry_price, leverage, order_type).
     Its `is_tradeable`-like affordances do not exist; there is no
     `to_signal`, `to_order`, or `from_*` constructor.
  3. Every ledger of reasons is grounded: a rejection code that is not in
     `CODE_EXPLANATIONS` gets an "unexplained, ask the operator" verdict —
     we never invent a rationale for a code the table does not know.

An LLM surface can sit ON TOP of this later (Book-3, optional). The
explanations below are deterministic so the LLM never becomes the first
source of a claim about the trader's own behavior.

Pure module: no IO, no clock of its own (callers pass `ts`), no network,
no keys. It explains; it never trades.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Sequence

ADVISORY_KIND_REJECTION = "rejection_explainer"
ADVISORY_KIND_PROFILE = "profile_explainer"

# Explanations for RiskGate rejection codes, keyed by the exact code string
# the gate emits (src/execution/risk_gate.py). The honesty invariant: text
# explains the MECHANISM the gate enforces — never advice on how to relax it.
# tests/test_advisory.py pins this table against the live gate source: every
# key must exist in the gate, every gate rejection code must have a key.
CODE_EXPLANATIONS: dict[str, str] = {
    "KILL_SWITCH_ACTIVE":
        "The kill switch is latched. All new orders are refused while it is "
        "active; this is the operator's stop, not a transient condition.",
    "COOLDOWN_ACTIVE":
        "A post-kill-switch cooldown is still running. New orders are refused "
        "until the cooldown window expires.",
    "DRAWDOWN_BREACH":
        "The trailing drawdown regime multiplier has cut sizing below the "
        "increment required for this order, or hard-halted new orders after "
        "exceeding the drawdown budget.",
    "CONFIDENCE_TOO_LOW":
        "The decision's confidence is below the configured floor. Low-"
        "conviction decisions are refused rather than sized down.",
    "INVALID_ORDER_SIZE":
        "The order size is missing, malformed, or non-positive, so no sane "
        "notional could be derived from it.",
    "REGIME_SIZE_CAP":
        "The current regime profile caps this order's size above its own "
        "allowance; the gate clipped the request instead of approving it.",
    "POSITION_TOO_LARGE":
        "Adding this order would exceed the maximum single-position notional.",
    "DAILY_TRADE_LIMIT_EXCEEDED":
        "The account has traded the maximum number of trades allowed per "
        "UTC day.",
    "DAILY_VOLUME_LIMIT_EXCEEDED":
        "Adding this order would exceed the maximum daily traded volume.",
    "DAILY_LOSS_LIMIT_EXCEEDED":
        "The accumulated daily loss has reached the configured cap; new risk "
        "is refused until the next UTC day.",
    "FAT_FINGER_REJECTED":
        "Entry would occur farther from the trusted reference price than the "
        "slippage/fat-finger collar allows — likely a broken feed or an "
        "unrealistic price.",
    "NO_PRICE_REFERENCE":
        "No trusted price reference existed to vet this order against. "
        "Missing data fails closed; an unverifiable price is treated as a "
        "bad price.",
    "INVALID_LEVERAGE":
        "The order's leverage field is malformed or out of the accepted "
        "numeric range.",
    "LEVERAGE_EXCEEDED":
        "The order's leverage exceeds the configured maximum.",
    "SLIPPAGE_EXCEEDED":
        "The vetted reference price moved beyond the per-fill slippage "
        "collar since the mandate was computed; the gate refused the price "
        "instead of chasing it.",
    "REDUCE_ONLY_VIOLATION":
        "The mandate is marked reduce-only but would increase exposure — "
        "checked symmetrically in both directions.",
    "INVALID_MANDATE_TYPE":
        "A mandate field's value is the wrong type (e.g. stringified number, "
        "bool-as-number, or NaN), so its magnitude could not be trusted.",
    "REFERENCE_VELOCITY_EXCEEDED":
        "The price reference moved faster than the maximum observed velocity "
        "between trusted observations; the observation is suspected data and "
        "was rejected (sustained real moves still converge — this is a data "
        "suspicion, not a lost fill).",
    "ASSET_NOT_ALLOWED":
        "The instrument is not on the allowlist (exact-match semantics — "
        "base-asset family matching does not exist). Only explicitly allowed "
        "assets and companions trade.",
    "STALE_PRICE":
        "The price reference is too old to vet this order against. A stale "
        "price is treated as no price: fail closed, wait for fresh data.",
}

_UNEXPLAINED_TEMPLATE = (
    "code {code!r} is not in the advisory explanation table — the reason is "
    "NOT being guessed; ask the operator to extend the table with this code."
)

_FORBIDDEN_REASONS = (
    "retry at a higher size",
    "relax the gate",
    "override the gate",
    "the gate is wrong",
    "retry at a higher price",  # defense-in-depth marker, see _no_relax_advice
)


@dataclass(frozen=True)
class Advisory:
    """A human-readable explanation. NEVER order-shaped.

    Deliberately absent fields (pinned by test): `direction`,
    `confidence_bps`, `size`, `size_usd`, `entry_price`, `leverage`,
    `order_type`, `side`. There is no `to_signal`, `to_order`, `from_signal`,
    or `from_order` method, and no `is_tradeable` property.
    """

    advisory_id: str          # deterministic: sha256 over the canonical body
    created_ts: float         # caller-supplied
    kind: str                 # ADVISORY_KIND_* constant
    subject: str              # what this is about, in words (e.g. asset+event)
    headline: str
    evidence: tuple[tuple[str, str], ...]   # (provenance, fact) pairs
    explanation: str          # deterministic narrative, may span codes

    def to_dict(self) -> dict:
        return {
            "advisory_id": self.advisory_id,
            "created_ts": self.created_ts,
            "kind": self.kind,
            "subject": self.subject,
            "headline": self.headline,
            "evidence": [[p, f] for p, f in self.evidence],
            "explanation": self.explanation,
        }


def _canonical_id(ts: float, kind: str, subject: str, evidence: Sequence) -> str:
    body = repr((ts, kind, subject, evidence))
    return "adv_" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _check_ts(ts: float, *, label: str) -> float:
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        raise TypeError(f"{label} must be a real number, got {type(ts).__name__}")
    ts_f = float(ts)
    if not math.isfinite(ts_f):
        raise ValueError(f"{label} must be finite, got {ts!r}")
    return ts_f


def _check_words(value, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _no_relax_advice(text: str) -> None:
    """Honesty invariant floor: an explanation must never carry relax/override
    advice. If the table ever grows a sentence like that, fail loudly."""
    lowered = text.lower()
    for phrase in _FORBIDDEN_REASONS:
        if phrase in lowered:
            raise ValueError(
                f"advisory text must never include relax/override advice "
                f"(found {phrase!r})"
            )


def explain_rejection(
    *,
    asset: str,
    ts: float,
    codes: Sequence[str],
    note: str = "",
) -> Advisory:
    """Explain why the risk gate refused a decision. Deterministic.

    Unknown codes produce an explicit "unexplained" verdict rather than an
    invented rationale. Duplicate codes are listed once (counted, not
    repeated). Fail-closed on empty/typed-bad inputs.
    """
    asset = _check_words(asset, label="asset")
    ts_f = _check_ts(ts, label="ts")
    if not codes:
        raise ValueError("codes must be non-empty — there is nothing to explain")
    if not isinstance(codes, Sequence) or isinstance(codes, str):
        raise TypeError("codes must be a sequence of code strings")
    for code in codes:
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"each code must be a non-empty string, got {code!r}")

    unique_codes = list(dict.fromkeys(codes))
    evidence: list[tuple[str, str]] = [
        ("risk_gate", f"rejection code {code}")
        for code in unique_codes
    ]
    if note:
        evidence.append(("operator_note", note))

    parts: list[str] = []
    for code in unique_codes:
        reason = CODE_EXPLANATIONS.get(code)
        if reason is None:
            parts.append(_UNEXPLAINED_TEMPLATE.format(code=code))
        else:
            parts.append(f"{code}: {reason}")
    if len(unique_codes) > 1:
        parts.insert(0, f"The gate refused this order {len(unique_codes)} independent ways:")

    explanation = " ".join(parts)
    _no_relax_advice(explanation)

    n = len(unique_codes)
    plural = "check" if n == 1 else "checks"
    return Advisory(
        advisory_id=_canonical_id(ts_f, ADVISORY_KIND_REJECTION, asset, evidence),
        created_ts=ts_f,
        kind=ADVISORY_KIND_REJECTION,
        subject=f"{asset} order refusal",
        headline=(
            f"{n} risk-gate {plural} refused the {asset} order; "
            f"no order was placed and the refusal is not to be retried blindly."
        ),
        evidence=tuple(evidence),
        explanation=explanation,
    )


def explain_profile(
    *,
    profile: str,
    ts: float,
    applies_to: str,
    note: str = "",
) -> Advisory:
    """Explain which curator profile is active and why. Deterministic."""
    profile = _check_words(profile, label="profile")
    applies_to = _check_words(applies_to, label="applies_to")
    ts_f = _check_ts(ts, label="ts")

    evidence: list[tuple[str, str]] = [("curator", f"active profile {profile!r}")]
    if note:
        evidence.append(("operator_note", note))
    return Advisory(
        advisory_id=_canonical_id(ts_f, ADVISORY_KIND_PROFILE, applies_to, evidence),
        created_ts=ts_f,
        kind=ADVISORY_KIND_PROFILE,
        subject=f"{applies_to} curator posture",
        headline=f"Curator profile {profile!r} is active for {applies_to}.",
        evidence=tuple(evidence),
        explanation=(
            f"The curator selected {profile!r}. Sizing, confidence floor, and "
            f"allowlist behavior follow that profile's knobs; the deterministic "
            f"risk gate still enforces every check independently of the profile."
        ),
    )
