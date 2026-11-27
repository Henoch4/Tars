"""Fail-closed parsers for external honeypot/tax safety signals.

These adapters only normalize explicitly reported provider fields. They do
not infer safety from scores, liquidity, or a provider's generic "safe" label.
Results remain secondary evidence with source and observation metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
import json
import os
import urllib.parse
import urllib.request
import urllib.error
from typing import Any


@dataclass(frozen=True)
class ExternalSafetySignal:
    source: str
    status: str  # ok, unsafe, unknown
    honeypot: bool | None = None
    transfer_tax_pct: float | None = None
    reason: str | None = None
    observed_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "status": self.status,
            "honeypot": self.honeypot,
            "transfer_tax_pct": self.transfer_tax_pct,
            "reason": self.reason,
            "observed_at": self.observed_at,
        }

@dataclass(frozen=True)
class AuthoritativeSellabilityEvidence:
    """Provider contract for an explicit sellability verdict.

    This is intentionally separate from secondary safety signals: ``sellable``
    is admissible only with fresh observation metadata and test conditions.
    """
    source: str
    status: str  # sellable, not_sellable, unsupported, unknown, stale, provider_error
    observed_at: float
    observation_slot: int | None = None
    test_conditions: str = ""
    reason: str = ""

    def admissible(self, *, now: float, max_age_s: int = 900) -> bool:
        return (self.status == "sellable" and bool(self.source.strip())
                and bool(self.test_conditions.strip())
                and isinstance(self.observed_at, (int, float))
                and 0 <= now - self.observed_at <= max_age_s)

def validate_authoritative_sellability(evidence: AuthoritativeSellabilityEvidence,
                                       *, now: float, max_age_s: int = 900) -> AuthoritativeSellabilityEvidence:
    if evidence.status not in {"sellable", "not_sellable", "unsupported", "unknown", "stale", "provider_error"}:
        raise ValueError("invalid sellability status")
    if not evidence.source or not evidence.reason:
        raise ValueError("sellability evidence requires source and reason")
    if evidence.status == "sellable" and not evidence.test_conditions:
        raise ValueError("sellable evidence requires test conditions")
    if evidence.status == "sellable" and now - evidence.observed_at > max_age_s:
        return AuthoritativeSellabilityEvidence(evidence.source, "stale", evidence.observed_at,
                                                evidence.observation_slot, evidence.test_conditions,
                                                "observation exceeded freshness limit")
    return evidence

def parse_authoritative_sellability(payload: Any, *, source: str,
                                    observed_at: float, observation_slot: int | None = None) -> AuthoritativeSellabilityEvidence:
    """Parse only an explicit provider sellability contract; scores are ignored."""
    if not isinstance(payload, dict):
        return AuthoritativeSellabilityEvidence(source, "provider_error", observed_at,
                                                observation_slot, reason="malformed response")
    raw = payload.get("sellability")
    conditions = payload.get("test_conditions")
    reason = payload.get("reason")
    if raw not in {"sellable", "not_sellable", "unsupported", "unknown", "stale", "provider_error"}:
        return AuthoritativeSellabilityEvidence(source, "unknown", observed_at,
                                                observation_slot, str(conditions or ""),
                                                "explicit sellability field absent or invalid")
    if not isinstance(reason, str) or not reason.strip():
        return AuthoritativeSellabilityEvidence(source, "unknown", observed_at,
                                                observation_slot, str(conditions or ""),
                                                "provider reason missing")
    return AuthoritativeSellabilityEvidence(source, raw, observed_at, observation_slot,
                                            str(conditions or ""), reason)

def compare_sellability_evidence(authoritative: AuthoritativeSellabilityEvidence,
                                 *, token2022_sell_restrictions: str,
                                 replay_sell_ok: bool | None) -> AuthoritativeSellabilityEvidence:
    """Any disagreement with chain inspection or replay becomes a conflict."""
    if authoritative.status != "sellable":
        return authoritative
    if token2022_sell_restrictions not in {"clear", "unknown"}:
        return AuthoritativeSellabilityEvidence(authoritative.source, "unknown", authoritative.observed_at,
                                                authoritative.observation_slot, authoritative.test_conditions,
                                                "conflict with Token-2022 inspection")
    if replay_sell_ok is False:
        return AuthoritativeSellabilityEvidence(authoritative.source, "unknown", authoritative.observed_at,
                                                authoritative.observation_slot, authoritative.test_conditions,
                                                "conflict with synthetic replay")
    return authoritative


def unknown_signal(source: str, reason: str) -> ExternalSafetySignal:
    return ExternalSafetySignal(source, "unknown", reason=reason,
                                observed_at=time.time())


def combine_external_signals(*signals: ExternalSafetySignal) -> ExternalSafetySignal:
    """Combine secondary provider signals without upgrading their authority.

    Any disagreement is explicit ``unknown``/``conflict``. A provider's
    positive result never overrides chain or simulation blockers elsewhere.
    """
    usable = tuple(s for s in signals if isinstance(s, ExternalSafetySignal))
    if not usable:
        return unknown_signal("external", "no provider signals")
    if any(s.status == "unsafe" for s in usable) and any(s.status == "ok" for s in usable):
        return ExternalSafetySignal("external", "unknown", reason="provider conflict",
                                    observed_at=max(s.observed_at for s in usable))
    if any(s.status == "unsafe" for s in usable):
        return ExternalSafetySignal("external", "unsafe", reason="provider reports sell risk",
                                    observed_at=max(s.observed_at for s in usable))
    if all(s.status == "ok" for s in usable):
        taxes = {s.transfer_tax_pct for s in usable if s.transfer_tax_pct is not None}
        if len(taxes) > 1:
            return ExternalSafetySignal("external", "unknown", reason="tax conflict",
                                        observed_at=max(s.observed_at for s in usable))
        return ExternalSafetySignal("external", "ok", False,
                                    next(iter(taxes), None), "corroborated providers",
                                    max(s.observed_at for s in usable))
    return ExternalSafetySignal("external", "unknown", reason="provider evidence incomplete",
                                observed_at=max(s.observed_at for s in usable))


def query_provider(fetch, parser, *, source: str,
                   now: float | None = None) -> ExternalSafetySignal:
    """Run an injected provider call and normalize all failure modes."""
    observed = time.time() if now is None else float(now)
    try:
        signal = parser(fetch())
    except TimeoutError:
        return ExternalSafetySignal(source, "unknown", reason="timeout", observed_at=observed)
    except Exception as exc:
        return ExternalSafetySignal(source, "unknown",
                                    reason=f"provider_error:{type(exc).__name__}",
                                    observed_at=observed)
    if not isinstance(signal, ExternalSafetySignal):
        return ExternalSafetySignal(source, "unknown", reason="invalid parser result",
                                    observed_at=observed)
    return ExternalSafetySignal(signal.source or source, signal.status,
                                signal.honeypot, signal.transfer_tax_pct,
                                signal.reason, observed)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if 0.0 <= out <= 100.0 else None


def parse_goplus(payload: Any) -> ExternalSafetySignal:
    """Parse explicit GoPlus-style ``result`` fields only."""
    source = "goplus"
    root = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(root, dict) and not any(k in root for k in
                                          ("is_honeypot", "sell_tax", "transfer_tax")):
        # GoPlus commonly keys result by mint address.
        values = [v for v in root.values() if isinstance(v, dict)]
        root = values[0] if len(values) == 1 else None
    if not isinstance(root, dict):
        return unknown_signal(source, "missing result")
    honeypot_raw = root.get("is_honeypot")
    honeypot = honeypot_raw in ("1", 1, True) if honeypot_raw is not None else None
    tax = _number(root.get("sell_tax", root.get("transfer_tax")))
    cannot_buy = root.get("cannot_buy") in ("1", 1, True)
    cannot_sell = root.get("cannot_sell_all") in ("1", 1, True)
    if honeypot is None and tax is None and not cannot_buy and not cannot_sell:
        return unknown_signal(source, "explicit tax/honeypot fields absent")
    if (honeypot is True or cannot_buy or cannot_sell or
            (tax is not None and tax >= 100.0)):
        return ExternalSafetySignal(source, "unsafe", honeypot, tax, "provider reports sell risk")
    return ExternalSafetySignal(source, "ok", False, tax)


class GoplusSolanaClient:
    """Bounded GoPlus Solana Token Security client; never logs its token."""
    def __init__(self, token: str | None = None, endpoint: str | None = None,
                 timeout_ms: int | None = None, opener=None,
                 auth_scheme: str | None = None):
        self.token = (token if token is not None else os.getenv("GOPLUS_SOLANA_TOKEN", "")).strip()
        raw_endpoint = endpoint or os.getenv(
            "GOPLUS_SOLANA_ENDPOINT",
            "https://api.gopluslabs.io/api/v1/solana/token_security")
        self.endpoint = (raw_endpoint or "").strip()
        self.auth_scheme = (auth_scheme if auth_scheme is not None else
                            os.getenv("GOPLUS_SOLANA_AUTH_SCHEME", "")).strip()
        raw_timeout = timeout_ms if timeout_ms is not None else os.getenv(
            "GOPLUS_SOLANA_TIMEOUT_MS", "5000")
        try:
            parsed_timeout = int(raw_timeout)
        except (TypeError, ValueError):
            parsed_timeout = 5000
        self.timeout_s = max(0.1, parsed_timeout / 1000.0)
        self._opener = opener or urllib.request.urlopen

    def fetch(self, mint: str) -> dict:
        if not self.token:
            raise RuntimeError("GoPlus token not configured")
        query = urllib.parse.urlencode({"contract_addresses": mint})
        req = urllib.request.Request(
            f"{self.endpoint}?{query}",
            headers={"Accept": "application/json", "Authorization": (
                f"{self.auth_scheme} {self.token}" if self.auth_scheme else self.token)},
            method="GET")
        try:
            with self._opener(req, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GoPlus HTTP {exc.code}") from exc
        except Exception as exc:
            raise RuntimeError(f"GoPlus transport {type(exc).__name__}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("GoPlus malformed response")
        return payload


class RugcheckSafetyClient:
    """Read-only RugCheck report client for explicit risk evidence."""
    def __init__(self, timeout_ms: int = 5000, opener=None):
        self.timeout_s = max(0.1, int(timeout_ms) / 1000.0)
        self._opener = opener or urllib.request.urlopen

    def fetch(self, mint: str) -> dict:
        if not isinstance(mint, str) or not mint.strip():
            raise RuntimeError("invalid mint")
        url = "https://api.rugcheck.xyz/v1/tokens/" + urllib.parse.quote(mint.strip(), safe="") + "/report"
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with self._opener(req, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"RugCheck HTTP {exc.code}") from exc
        except Exception as exc:
            raise RuntimeError(f"RugCheck transport {type(exc).__name__}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("RugCheck malformed response")
        return payload


def parse_rugcheck(payload: Any) -> ExternalSafetySignal:
    """Parse explicit RugCheck risk entries; scores alone are ignored."""
    source = "rugcheck"
    if not isinstance(payload, dict):
        return unknown_signal(source, "payload is not an object")
    risks = payload.get("risks")
    if not isinstance(risks, list):
        return unknown_signal(source, "risk list absent")
    text = " ".join(str(r.get("name", r.get("description", ""))).lower()
                     for r in risks if isinstance(r, dict))
    tax = None
    for item in risks:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).lower()
        if "tax" in name or "fee" in name:
            tax = _number(item.get("value", item.get("description")))
    if any(word in text for word in ("honeypot", "cannot sell", "sell blocked")):
        return ExternalSafetySignal(source, "unsafe", True, tax, "provider risk entry")
    if tax is not None and tax > 0.0:
        return ExternalSafetySignal(source, "unsafe", False, tax, "provider tax risk entry")
    return unknown_signal(source, "no explicit honeypot/tax verdict")
