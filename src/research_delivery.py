"""Scheduled-research delivery (V18, vibe-trading stealable).

Propose/confirm scheduling for planned research deliverables: a client
proposes research once, receives an OPAQUE confirm/deny token, and only a
deliberate confirm schedules the delivery. Nothing is delivered
fire-and-forget — every delivery ends in an immutable, auditable
`DeliveryReceipt` the caller records (typical home: `AuditLog.write(
"research_delivered", receipt.to_dict())`).

Doctrine this encodes:
  1. **Delivery must be auditable, not fire-and-forget.** `DeliveryReceipt`
     is the unit of evidence: who delivered what, when, under which
     proposal. A delivery without a prior confirm raises `UnknownProposal`.
  2. **The token is opaque.** The desk stores only its sha256 digest; the
     plaintext token lives with the proposer, never in the registry, never
     in any `to_dict` output.
  3. **One-shot lifecycle, no silent revive:**
        proposed ──confirm──▶ confirmed ──deliver──▶ delivered
             └─deny─▶ denied              └─expire/sweep──▶ expired
     Every state transition is terminal for that token; replays and
     dual-resolution raise typed errors, never no-op.
  4. **Fail-closed on inputs:** unknown kinds, non-finite timestamps, empty
     subjects, pre-schedule delivery, and post-window confirm/deliver all
     raise — nothing defaults into a pass.

Pure module: no IO, no clock of its own (callers pass `ts`), no network,
no keys. The registry state is in-process; durable persistence here is the
receipt itself — the caller writes `DeliveryReceipt.to_dict()` to the audit
trail, which is the had-it-happened record the audit doctrine wants.
"""
from __future__ import annotations

import hashlib
import math
import secrets
from dataclasses import dataclass
from typing import Literal

# Explicit allowlist — an unknown kind raises instead of passing.
KINDS = ("digest", "deep_dive", "trigger_watch")

# Windows (seconds). Tight on purpose: scheduling is an operator act that
# happens within minutes of the proposal, and letting confirm/deliver
# windows drift open is how stale research gets "delivered" later.
CONFIRM_WINDOW_S = 3600.0
DELIVER_LATENESS_S = 300.0

Status = Literal["proposed", "confirmed", "denied", "delivered", "expired"]


class DeskError(Exception):
    """Base for research-desk failures — every failure is typed."""


class UnknownProposal(DeskError):
    """Token/id does not match any live proposal."""


class ResolvedProposal(DeskError):
    """Proposal already terminal (confirmed/denied/delivered/expired)."""


class NotYetScheduled(DeskError):
    """Attempted before its scheduled-for timestamp."""


class ProposalExpired(DeskError):
    """Confirm window elapsed before the deliberate confirm."""


def _finite_ts(ts, *, label: str) -> float:
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        raise TypeError(f"{label} must be a real number, got {type(ts).__name__}")
    ts_f = float(ts)
    if not math.isfinite(ts_f):
        raise ValueError(f"{label} must be finite, got {ts!r}")
    return ts_f


def _word(value, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    kind: str
    subject: str
    proposed_ts: float
    scheduled_for_ts: float
    status: "Status"
    confirm_token_digest: str   # sha256(token) hex — plaintext NEVER stored

    def to_dict(self) -> dict:
        # Deliberately token-free: the registry view of a proposal never
        # includes anything a replay attacker could replay.
        return {
            "proposal_id": self.proposal_id,
            "kind": self.kind,
            "subject": self.subject,
            "proposed_ts": self.proposed_ts,
            "scheduled_for_ts": self.scheduled_for_ts,
            "status": self.status,
            "confirm_token_digest": self.confirm_token_digest,
        }


@dataclass(frozen=True)
class DeliveryReceipt:
    """The auditable unit of research delivery. Callers write this to the
    audit trail; it is immutable once created."""

    receipt_id: str
    proposal_id: str
    kind: str
    subject: str
    scheduled_for_ts: float
    delivered_ts: float
    payload_digest: str

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "proposal_id": self.proposal_id,
            "kind": self.kind,
            "subject": self.subject,
            "scheduled_for_ts": self.scheduled_for_ts,
            "delivered_ts": self.delivered_ts,
            "payload_digest": self.payload_digest,
        }


@dataclass
class _Live:
    kind: str
    subject: str
    proposed_ts: float
    scheduled_for_ts: float
    token_digest: str
    status: "Status"


class ResearchDesk:
    """Propose/confirm scheduler. One instance per surface; state is
    in-process (auditable persistence is the receipt, held by the caller)."""

    def propose(
        self,
        *,
        kind: str,
        subject: str,
        ts: float,
        scheduled_for_ts: float,
    ) -> tuple[Proposal, str]:
        """Register a proposal. Returns (proposal, plaintext_confirm_token).
        The token is opaque: the caller must hand it to the operator and MUST
        NOT log or persist it (only its digest lives here)."""
        if kind not in KINDS:
            raise ValueError(
                f"unknown proposal kind {kind!r} — allowed: {KINDS}"
            )
        subject = _word(subject, label="subject")
        ts_f = _finite_ts(ts, label="ts")
        sched_f = _finite_ts(scheduled_for_ts, label="scheduled_for_ts")
        if sched_f < ts_f:
            raise ValueError("scheduled_for_ts must not precede propose ts")

        token = secrets.token_hex(16)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        proposal_id = "res_" + hashlib.sha256(
            repr((ts_f, kind, subject, sched_f, digest)).encode("utf-8")
        ).hexdigest()[:16]

        live = _Live(
            kind=kind, subject=subject, proposed_ts=ts_f,
            scheduled_for_ts=sched_f, token_digest=digest,
            status="proposed",
        )
        self._live[proposal_id] = live
        self._tokens[digest] = proposal_id

        return Proposal(
            proposal_id=proposal_id,
            kind=kind,
            subject=subject,
            proposed_ts=ts_f,
            scheduled_for_ts=sched_f,
            status="proposed",
            confirm_token_digest=digest,
        ), token

    # -- resolution ---------------------------------------------------------

    def _lookup_by_token(self, token: str) -> _Live:
        _word(token, label="token")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        proposal_id = self._tokens.get(digest)
        if proposal_id is None:
            raise UnknownProposal("no live proposal for that token")
        live = self._live[proposal_id]
        if live.status != "proposed":
            raise ResolvedProposal(
                f"proposal {proposal_id} is already {live.status}"
            )
        return live

    def confirm(self, token: str, ts: float) -> Proposal:
        """Operator's deliberate yes."""
        ts_f = _finite_ts(ts, label="ts")
        live = self._lookup_by_token(token)
        if ts_f < live.proposed_ts:
            raise ValueError("confirm ts precedes propose ts")
        if ts_f > live.proposed_ts + CONFIRM_WINDOW_S:
            live.status = "expired"
            raise ProposalExpired(
                f"confirm window ({CONFIRM_WINDOW_S:.0f}s) elapsed"
            )
        live.status = "confirmed"
        return self._frozen(live)

    def deny(self, token: str, *, ts: float, reason: str) -> Proposal:
        """Operator's explicit no. `reason` is required (a bare deny tells
        the future nothing); it is re-raised to the caller to record."""
        _word(reason, label="reason")
        ts_f = _finite_ts(ts, label="ts")
        live = self._lookup_by_token(token)
        if ts_f < live.proposed_ts:
            raise ValueError("deny ts precedes propose ts")
        if ts_f > live.proposed_ts + CONFIRM_WINDOW_S:
            live.status = "expired"
            raise ProposalExpired(
                f"decision window ({CONFIRM_WINDOW_S:.0f}s) elapsed"
            )
        live.status = "denied"
        return self._frozen(live)

    def deliver(
        self,
        proposal_id: str,
        *,
        ts: float,
        payload_digest: str,
    ) -> DeliveryReceipt:
        """Materialize the delivery. ONLY a confirmed, due proposal delivers;
        the returned receipt is the caller's audit record."""
        _word(proposal_id, label="proposal_id")
        _word(payload_digest, label="payload_digest")
        ts_f = _finite_ts(ts, label="ts")
        live = self._live.get(proposal_id)
        if live is None or live.status == "expired":
            raise UnknownProposal(f"no live proposal {proposal_id}")
        if live.status == "proposed":
            raise ResolvedProposal(
                f"{proposal_id} was never confirmed — no delivery is "
                f"possible from an unconfirmed proposal"
            )
        if live.status in ("denied", "delivered"):
            raise ResolvedProposal(
                f"proposal {proposal_id} is {live.status}; terminal"
            )
        if ts_f < live.scheduled_for_ts:
            raise NotYetScheduled(
                f"{proposal_id} schedules at {live.scheduled_for_ts:.0f}; "
                f"attempted delivery at {ts_f:.0f}"
            )
        if ts_f > live.scheduled_for_ts + DELIVER_LATENESS_S:
            live.status = "expired"
            raise ProposalExpired(
                f"delivery too late ({DELIVER_LATENESS_S:.0f}s grace "
                f"exceeded) — re-propose rather than deliver stale research"
            )
        live.status = "delivered"
        receipt_id = "rcpt_" + hashlib.sha256(
            repr((proposal_id, ts_f, payload_digest)).encode("utf-8")
        ).hexdigest()[:16]
        return DeliveryReceipt(
            receipt_id=receipt_id,
            proposal_id=proposal_id,
            kind=live.kind,
            subject=live.subject,
            scheduled_for_ts=live.scheduled_for_ts,
            delivered_ts=ts_f,
            payload_digest=payload_digest,
        )

    def sweep(self, ts: float) -> list[tuple[str, str]]:
        """Expire every stale proposal. Returns [(id, status)] of what it
        retired — the caller writes those to the audit trail too."""
        ts_f = _finite_ts(ts, label="ts")
        retired: list[tuple[str, str]] = []
        for proposal_id, live in self._live.items():
            if live.status == "proposed" and (
                ts_f > live.proposed_ts + CONFIRM_WINDOW_S
            ):
                live.status = "expired"
                retired.append((proposal_id, "expired"))
            elif live.status == "confirmed" and (
                ts_f > live.scheduled_for_ts + DELIVER_LATENESS_S
            ):
                live.status = "expired"
                retired.append((proposal_id, "expired_undelivered"))
        return retired

    def view(self, proposal_id: str) -> Proposal:
        live = self._live.get(proposal_id)
        if live is None:
            raise UnknownProposal(f"no proposal {proposal_id}")
        return self._frozen(live)

    def _frozen(self, live: _Live) -> Proposal:
        return Proposal(
            proposal_id=self._tokens[live.token_digest],
            kind=live.kind,
            subject=live.subject,
            proposed_ts=live.proposed_ts,
            scheduled_for_ts=live.scheduled_for_ts,
            status=live.status,
            confirm_token_digest=live.token_digest,
        )

    def __init__(self) -> None:
        self._live: dict[str, _Live] = {}
        self._tokens: dict[str, str] = {}

    @property
    def open_count(self) -> int:
        return sum(1 for l in self._live.values() if l.status == "proposed")
