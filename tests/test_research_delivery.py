"""Guard tests for the scheduled-research desk (V18).

Invariants under test:
  - delivery is impossible without a prior confirm; an unconfirmed proposal
    can neither fire early nor late
  - the confirm token is opaque: only its sha256 digest is stored and no
    view of the desk or of a proposal ever contains the plaintext
  - one-shot lifecycle: replays / dual-resolution / unknown tokens raise
    typed errors, never no-op
  - windows fail closed: late confirm expires, late deliver expires, and
    re-delivery of a delivered proposal is refused
  - DeliveryReceipt.to_dict() is the auditable record (id, subject, times,
    payload digest) with no token material
"""
import pytest

from src.research_delivery import (
    CONFIRM_WINDOW_S,
    DELIVER_LATENESS_S,
    DeliveryReceipt,
    KINDS,
    NotYetScheduled,
    ProposalExpired,
    ResearchDesk,
    ResolvedProposal,
    UnknownProposal,
)


def _propose(desk, ts=1_700_000_000.0, kind="digest", subject="Breaking Bad: memecoin exits",
             scheduled_for_ts=None):
    """propose without artifact drift: scheduled_for_ts defaults to ts+600."""
    return desk.propose(
        kind=kind, subject=subject, ts=ts,
        scheduled_for_ts=(ts + 600.0) if scheduled_for_ts is None else scheduled_for_ts,
    )


def test_propose_returns_opaque_token_and_a_digest_only_view():
    desk = ResearchDesk()
    proposal, token = _propose(desk)
    assert len(token) == 32 and all(c in "0123456789abcdef" for c in token)
    assert proposal.status == "proposed"
    assert proposal.proposal_id.startswith("res_")
    # registry view carries ONLY the digest
    assert proposal.confirm_token_digest == __import__("hashlib").sha256(
        token.encode()).hexdigest()
    assert token not in repr(proposal) and token not in str(proposal.to_dict())
    # the digest is NOT the token
    assert proposal.confirm_token_digest != token


def test_happy_path_propose_confirm_deliver_auditable_receipt():
    desk = ResearchDesk()
    ts = 1_700_000_000.0
    proposal, token = _propose(desk, ts=ts, scheduled_for_ts=ts + 600.0)
    confirmed = desk.confirm(token, ts=ts + 60.0)
    assert confirmed.status == "confirmed"
    receipt = desk.deliver(proposal.proposal_id, ts=ts + 600.0,
                           payload_digest="sha256:cafebabe")
    assert isinstance(receipt, DeliveryReceipt)
    assert receipt.proposal_id == proposal.proposal_id
    assert receipt.kind == "digest" and receipt.subject
    assert receipt.delivered_ts == ts + 600.0
    assert receipt.scheduled_for_ts == ts + 600.0
    d = receipt.to_dict()
    assert set(d) == {
        "receipt_id", "proposal_id", "kind", "subject",
        "scheduled_for_ts", "delivered_ts", "payload_digest",
    }
    assert token not in repr(d)          # auditable record carries no token
    assert receipt.receipt_id.startswith("rcpt_")


def test_unconfirmed_proposal_cannot_deliver():
    desk = ResearchDesk()
    ts = 1_700_000_000.0
    proposal, _token = _propose(desk, ts=ts, scheduled_for_ts=ts + 100.0)
    with pytest.raises(ResolvedProposal):
        desk.deliver(proposal.proposal_id, ts=ts + 100.0, payload_digest="x")


def test_delivery_before_schedule_fails_closed():
    desk = ResearchDesk()
    ts = 1_700_000_000.0
    proposal, token = _propose(desk, ts=ts, scheduled_for_ts=ts + 600.0)
    desk.confirm(token, ts=ts + 1.0)
    with pytest.raises(NotYetScheduled):
        desk.deliver(proposal.proposal_id, ts=ts + 599.0, payload_digest="x")


def test_delivery_after_grace_expires_and_is_refused():
    desk = ResearchDesk()
    ts = 1_700_000_000.0
    proposal, token = _propose(desk, ts=ts, scheduled_for_ts=ts + 600.0)
    desk.confirm(token, ts=ts + 1.0)
    late = ts + 600.0 + DELIVER_LATENESS_S + 1.0
    with pytest.raises(ProposalExpired):
        desk.deliver(proposal.proposal_id, ts=late, payload_digest="x")
    from src.research_delivery import Proposal as _P
    assert desk.view(proposal.proposal_id).status == "expired"


def test_confirm_unknown_token_raises():
    desk = ResearchDesk()
    with pytest.raises(UnknownProposal):
        desk.confirm("0" * 32, ts=0.0)


def test_replay_confirm_is_refused():
    desk = ResearchDesk()
    ts = 1_700_000_000.0
    _proposal, token = _propose(desk, ts=ts)
    desk.confirm(token, ts=ts + 1.0)
    with pytest.raises(ResolvedProposal):
        desk.confirm(token, ts=ts + 2.0)
    with pytest.raises(ResolvedProposal):
        desk.deny(token, ts=ts + 3.0, reason="changed my mind")


def test_deny_then_deliver_refused():
    desk = ResearchDesk()
    ts = 1_700_000_000.0
    proposal, token = _propose(desk, ts=ts)
    denied = desk.deny(token, ts=ts + 5.0, reason="worse sources than promised")
    assert denied.status == "denied"
    with pytest.raises(ResolvedProposal):
        desk.deliver(proposal.proposal_id, ts=ts + 900.0, payload_digest="x")


def test_deny_requires_a_reason_and_bad_ts_fails_closed():
    desk = ResearchDesk()
    ts = 1_700_000_000.0
    _proposal, token = _propose(desk, ts=ts)
    with pytest.raises(ValueError):
        desk.deny(token, ts=ts, reason="   ")
    with pytest.raises((TypeError, ValueError)):
        desk.confirm(token, ts=float("nan"))
    with pytest.raises((TypeError, ValueError)):
        desk.confirm(token, ts="tomorrow")


def test_confirm_after_window_is_expiration_not_approval():
    desk = ResearchDesk()
    ts = 1_700_000_000.0
    _proposal, token = _propose(desk, ts=ts)
    with pytest.raises(ProposalExpired):
        desk.confirm(token, ts=ts + CONFIRM_WINDOW_S + 1.0)


@pytest.mark.parametrize("kwargs", [
    dict(kind="usual_live_ammo", subject="x"),
    dict(kind=None, subject="x"),
    dict(kind="digest", subject=""),
    dict(kind="digest", subject="x", ts=float("nan")),
    dict(kind="digest", subject="x", scheduled_for_ts=0.0),  # precedes ts=1e9
])
def test_propose_fail_closed(kwargs):
    desk = ResearchDesk()
    with pytest.raises((ValueError, TypeError)):
        desk.propose(kind=kwargs.get("kind", "digest"),
                     subject=kwargs.get("subject", "x"),
                     ts=kwargs.get("ts", 1_700_000_000.0),
                     scheduled_for_ts=kwargs.get(
                         "scheduled_for_ts", 1_700_000_100.0))


def test_kinds_are_allowlisted_not_extended_by_accident():
    assert KINDS == ("digest", "deep_dive", "trigger_watch")


def test_sweep_expires_late_proposals_and_reports_them():
    desk = ResearchDesk()
    ts = 1_700_000_000.0
    # A: confirmed and still inside its delivery grace at sweep time
    a, tok_a = _propose(desk, ts=ts, subject="sweep a",
                        scheduled_for_ts=ts + 5_000.0)
    # B: never confirmed
    b, _tok_b = _propose(desk, ts=ts, subject="sweep b")
    desk.confirm(tok_a, ts=ts + 1.0)
    # nothing swept: both still inside their windows
    assert desk.sweep(ts + CONFIRM_WINDOW_S / 2) == []
    retired = desk.sweep(ts + CONFIRM_WINDOW_S + 10.0)
    statuses = dict(retired)
    # B (unconfirmed, past its confirm window) is retired
    assert statuses.get(b.proposal_id) == "expired"
    assert desk.view(b.proposal_id).status == "expired"
    # A (confirmed, delivery still in the future) must NOT be retired early
    assert a.proposal_id not in statuses
    assert desk.view(a.proposal_id).status == "confirmed"
    # but past its delivery grace it is retired as expired_undelivered —
    # stale confirmed research is re-proposed, never delivered late
    late = a.scheduled_for_ts + DELIVER_LATENESS_S + 1.0
    force = {pid: s for pid, s in desk.sweep(late)}
    assert force.get(a.proposal_id) == "expired_undelivered"
    with pytest.raises(UnknownProposal):
        desk.deliver(a.proposal_id, ts=late + 1.0, payload_digest="x")


def test_view_unknown_id_raises():
    desk = ResearchDesk()
    with pytest.raises(UnknownProposal):
        desk.view("res_nope")


def test_receipt_is_frozen():
    desk = ResearchDesk()
    ts = 1_700_000_000.0
    proposal, token = _propose(desk, ts=ts, scheduled_for_ts=ts + 1.0)
    desk.confirm(token, ts=ts + 1.0)
    receipt = desk.deliver(proposal.proposal_id, ts=ts + 1.0, payload_digest="p")
    with pytest.raises(Exception):
        receipt.payload_digest = "tampered"


def test_subject_and_digest_typing_fail_closed():
    desk = ResearchDesk()
    ts = 1_700_000_000.0
    proposal, token = _propose(desk, ts=ts, scheduled_for_ts=ts + 1.0)
    desk.confirm(token, ts=ts + 1.0)
    with pytest.raises((ValueError, TypeError)):
        desk.deliver(proposal.proposal_id, ts=ts + 1.0, payload_digest=None)
    with pytest.raises((ValueError, TypeError)):
        desk.deliver(proposal.proposal_id, ts=ts + 1.0, payload_digest="   ")
    with pytest.raises((TypeError, ValueError)):
        desk.deliver(proposal.proposal_id, ts="now", payload_digest="ok")
    with pytest.raises((ValueError, TypeError)):
        desk.deliver("", ts=ts + 1.0, payload_digest="ok")
