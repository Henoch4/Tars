"""Completeness contract for GTA live-candidate safety data."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any


REQUIRED_FIELDS = (
    "mint", "liquidity_usd", "top10_hold_pct", "holder_count",
    "mint_authority_disabled", "freeze_authority_disabled", "honeypot",
    "transfer_tax_pct", "bundled_buy_pct", "creator_past_rugs",
    "creator_launch_count", "lp_locked",
)


@dataclass(frozen=True)
class EnrichmentResult:
    complete: bool
    missing: tuple[str, ...]
    invalid: tuple[str, ...]


@dataclass(frozen=True)
class TransferTaxEvidence:
    """Attributed transfer-tax evidence; simulation is never authoritative."""
    status: str  # authoritative_zero, authoritative_nonzero, simulation_only, unknown
    tax_pct: float | None
    sources: tuple[str, ...] = ()
    reason: str | None = None
    observed_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "tax_pct": self.tax_pct,
                "sources": self.sources, "reason": self.reason,
                "observed_at": self.observed_at}


def combine_transfer_tax_evidence(*, token2022: dict[str, Any] | None = None,
                                  jupiter_tax_pct: Any = None,
                                  simulation_tax_pct: Any = None) -> TransferTaxEvidence:
    """Combine explicit sources without converting unknown into zero tax."""
    authoritative: list[tuple[str, float]] = []
    sources: list[str] = []
    if isinstance(token2022, dict):
        if token2022.get("parse_error"):
            sources.append("token2022:parse_error")
        value = token2022.get("transfer_tax_pct")
        if isinstance(value, (int, float, str)) and _finite_number(value):
            try:
                parsed_token_tax = float(value)
            except (TypeError, ValueError):
                parsed_token_tax = None
            if parsed_token_tax is not None:
                authoritative.append(("token2022", parsed_token_tax))
            sources.append("token2022")
    if isinstance(jupiter_tax_pct, (int, float, str)) and _finite_number(jupiter_tax_pct):
        try:
            parsed_jupiter_tax = float(jupiter_tax_pct)
        except (TypeError, ValueError):
            parsed_jupiter_tax = None
        if parsed_jupiter_tax is not None:
            authoritative.append(("jupiter_audit", parsed_jupiter_tax))
        sources.append("jupiter_audit")
    if authoritative:
        values = {round(v, 8) for _, v in authoritative}
        if len(values) > 1:
            return TransferTaxEvidence("unknown", None, tuple(sources), "conflicting authoritative values", time.time())
        tax = authoritative[0][1]
        return TransferTaxEvidence(
            "authoritative_zero" if tax == 0.0 else "authoritative_nonzero",
            tax, tuple(sources), observed_at=time.time())
    if _finite_number(simulation_tax_pct):
        return TransferTaxEvidence("simulation_only", float(simulation_tax_pct),
                                   ("simulation",), "quote-output loss is not transfer-tax proof", time.time())
    return TransferTaxEvidence("unknown", None, tuple(sources), "no authoritative evidence", time.time())


def evidence_is_fresh(evidence: Any, *, now: float | None = None,
                      max_age_s: float = 900.0) -> bool:
    if not isinstance(evidence, dict) or evidence.get("status") in (None, "unknown", "conflicting"):
        return False
    observed = evidence.get("observed_at")
    if isinstance(observed, bool) or not isinstance(observed, (int, float)):
        return False
    current = time.time() if now is None else float(now)
    return 0.0 <= current - float(observed) <= float(max_age_s)


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def validate_safety_payload(candidate: dict[str, Any]) -> EnrichmentResult:
    """Validate the hard live-entry contract; no field defaults to safety."""
    missing = tuple(field for field in REQUIRED_FIELDS if field not in candidate or candidate[field] is None)
    invalid: list[str] = []
    if missing:
        return EnrichmentResult(False, missing, ())
    for field in ("liquidity_usd", "top10_hold_pct", "transfer_tax_pct", "bundled_buy_pct"):
        if not _finite_number(candidate[field]):
            invalid.append(field)
    if isinstance(candidate["holder_count"], bool) or not isinstance(candidate["holder_count"], int):
        invalid.append("holder_count")
    if candidate["mint_authority_disabled"] is not True:
        invalid.append("mint_authority_disabled")
    if candidate["freeze_authority_disabled"] is not True:
        invalid.append("freeze_authority_disabled")
    if candidate["honeypot"] is not False:
        invalid.append("honeypot")
    if float(candidate["transfer_tax_pct"]) != 0.0:
        invalid.append("transfer_tax_pct")
    if float(candidate["bundled_buy_pct"]) > 25.0:
        invalid.append("bundled_buy_pct")
    if isinstance(candidate["creator_past_rugs"], bool) or not isinstance(candidate["creator_past_rugs"], int):
        invalid.append("creator_past_rugs")
    if isinstance(candidate["creator_launch_count"], bool) or not isinstance(candidate["creator_launch_count"], int):
        invalid.append("creator_launch_count")
    if candidate["lp_locked"] is not True:
        invalid.append("lp_locked")
    return EnrichmentResult(not invalid, (), tuple(dict.fromkeys(invalid)))
