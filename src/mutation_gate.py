"""Mutation gate (CA7): lifecycle envelope for operator control-plane writes.

Every production mutation goes preview -> approval -> apply -> verify, with
an idempotency key, an audit record per transition, and a rollback where one
exists. Surfaces wired here are the ones that actually mutate production
state outside the per-trade hot path (per-trade counter increments are the
audited activity itself, not config changes, and stay ungated):

  - kill_switch.activate / deactivate (off-chain gate + on-chain mirror)
  - risk_params.set (on-chain setRiskParams — tightening-only on-chain)
  - asset_cap.set (on-chain setAssetCap — tightening-only on-chain)

Read/apply/rollback are injected per surface so tests run against fakes
while production wires the real RiskGate + OnchainLogger. Rollback of an
on-chain tightening is impossible by contract design (the chain forbids
loosening) — that returns an explicit IRREVERSIBLE verdict with the
pre-change snapshot, never a silent success.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# Proposal states. Terminal: VERIFIED (applied + confirmed), REJECTED,
# FAILED, ROLLED_BACK, IRREVERSIBLE (applied, no inverse exists).
PROPOSED = "proposed"
APPROVED = "approved"
APPLIED = "applied"
VERIFIED = "verified"
REJECTED = "rejected"
FAILED = "failed"
ROLLED_BACK = "rolled_back"
IRREVERSIBLE = "irreversible"


@dataclass
class MutationProposal:
    """One proposed production write, tracked from preview to verdict."""

    key: str  # idempotency key, caller-supplied, unique per intent
    surface: str  # "kill_switch" | "risk_params" | "asset_cap"
    action: str  # "activate" | "deactivate" | "set"
    params: dict  # action arguments
    requested_by: str
    state: str = PROPOSED
    approved_by: str | None = None
    self_approved: bool = False  # approver == requester: recorded, not hidden
    before: dict = field(default_factory=dict)  # state snapshot at propose time
    receipt: dict = field(default_factory=dict)  # applier receipt (apply output)
    error: str | None = None
    history: list = field(default_factory=list)  # (transition, detail) trail


@dataclass
class SurfaceHandlers:
    """Per-surface hooks. read/expect/apply/rollback all take the proposal
    action first so one surface serves several verbs; ceiling enforces the
    operator's numeric bounds before anything executes."""

    read: Callable[[str, dict], dict]  # (action, params) -> current snapshot
    expect: Callable[[str, dict], dict]  # (action, params) -> post-state verify expects
    apply: Callable[[str, dict], dict]  # (action, params) -> receipt (atomic-or-raise)
    rollback: Callable[[str, dict, dict], dict] | None  # None = no inverse exists
    ceiling: Callable[[str, dict], tuple[bool, str]]  # (action, params) -> (ok, reason)


def _require_nonempty_str(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"mutation {name} must be a non-empty string")


class MutationGate:
    """Owns proposals and enforces the preview/approve/apply/verify order."""

    def __init__(
        self,
        surfaces: dict[str, SurfaceHandlers],
        audit_log=None,
        approval_required: bool = True,
    ):
        if not surfaces:
            raise ValueError("mutation gate needs at least one surface")
        self._surfaces = dict(surfaces)
        self._audit_log = audit_log
        self._approval_required = approval_required
        self._proposals: dict[str, MutationProposal] = {}

    # -- lifecycle ----------------------------------------------------

    def propose(
        self,
        key: str,
        surface: str,
        action: str,
        params: dict,
        requested_by: str,
    ) -> MutationProposal:
        """Register intent. Duplicate keys return the existing proposal —
        proposing twice never creates a second mutation."""
        _require_nonempty_str(key, "key")
        _require_nonempty_str(requested_by, "requested_by")
        if key in self._proposals:
            return self._proposals[key]
        if surface not in self._surfaces:
            raise ValueError(f"unknown mutation surface {surface!r}")
        if not isinstance(params, dict):
            raise ValueError("mutation params must be a dict")
        proposal = MutationProposal(
            key=key, surface=surface, action=action,
            params=dict(params), requested_by=requested_by,
        )
        proposal.before = self._surfaces[surface].read(action, dict(params))
        proposal.history.append((PROPOSED, {"by": requested_by}))
        self._proposals[key] = proposal
        self._audit(proposal, PROPOSED, {"by": requested_by})
        return proposal

    def preview(self, key: str) -> dict:
        """Before/after diff with zero state change. Read-only by contract:
        calls only the surface reader/expecter, never the applier."""
        proposal = self._get(key)
        after = self._surfaces[proposal.surface].expect(
            proposal.action, dict(proposal.params))
        return {
            "key": key,
            "surface": proposal.surface,
            "action": proposal.action,
            "before": dict(proposal.before),
            "after": after,
            "state": proposal.state,
        }

    def approve(self, key: str, approver: str) -> MutationProposal:
        """Owner approval within ceilings. Records (but permits, for a solo
        operator) self-approval explicitly instead of hiding it."""
        proposal = self._get(key)
        _require_nonempty_str(approver, "approver")
        if proposal.state != PROPOSED:
            raise ValueError(
                f"cannot approve proposal {key!r} in state {proposal.state}"
            )
        handlers = self._surfaces[proposal.surface]
        ok, reason = handlers.ceiling(proposal.action, proposal.params)
        if not ok:
            proposal.state = REJECTED
            proposal.error = reason
            proposal.history.append((REJECTED, {"by": approver, "reason": reason}))
            self._audit(proposal, REJECTED, {"by": approver, "reason": reason})
            return proposal
        proposal.state = APPROVED
        proposal.approved_by = approver
        proposal.self_approved = (approver == proposal.requested_by)
        proposal.history.append((APPROVED, {
            "by": approver, "self_approved": proposal.self_approved,
        }))
        self._audit(proposal, APPROVED, {
            "by": approver, "self_approved": proposal.self_approved,
        })
        return proposal

    def apply(self, key: str) -> MutationProposal:
        """Execute once. Repeated applies return the cached receipt without
        re-executing — the idempotency key makes retries safe."""
        proposal = self._get(key)
        if proposal.state in (APPLIED, VERIFIED):
            proposal.history.append(("apply_replayed", {}))
            return proposal
        if self._approval_required and proposal.state != APPROVED:
            raise ValueError(
                f"cannot apply proposal {key!r} in state {proposal.state} "
                f"(approval required)"
            )
        handlers = self._surfaces[proposal.surface]
        try:
            receipt = handlers.apply(proposal.action, dict(proposal.params))
        except Exception as e:  # noqa: BLE001 — failure is recorded, not raised past
            proposal.state = FAILED
            proposal.error = f"{type(e).__name__}: {e}"
            proposal.history.append((FAILED, {"error": proposal.error}))
            self._audit(proposal, FAILED, {"error": proposal.error})
            return proposal
        proposal.receipt = dict(receipt) if isinstance(receipt, dict) else {"result": receipt}
        proposal.state = APPLIED
        proposal.history.append((APPLIED, {"receipt": proposal.receipt}))
        self._audit(proposal, APPLIED, {"receipt": proposal.receipt})
        self.verify(key)
        return proposal

    def verify(self, key: str) -> bool:
        """Re-read surface state and confirm the mutation landed. A mismatch
        marks FAILED (applied-but-unverified is not success)."""
        proposal = self._get(key)
        if proposal.state not in (APPLIED, VERIFIED):
            raise ValueError(
                f"cannot verify proposal {key!r} in state {proposal.state}"
            )
        handlers = self._surfaces[proposal.surface]
        current = handlers.read(proposal.action, dict(proposal.params))
        expected = handlers.expect(proposal.action, dict(proposal.params))
        matches = all(current.get(k) == v for k, v in expected.items())
        if matches:
            proposal.state = VERIFIED
            proposal.history.append((VERIFIED, {"observed": current}))
            self._audit(proposal, VERIFIED, {"observed": current})
            return True
        proposal.state = FAILED
        proposal.error = f"verify mismatch: observed {current}, expected {expected}"
        proposal.history.append((FAILED, {"error": proposal.error}))
        self._audit(proposal, FAILED, {"error": proposal.error})
        return False

    def rollback(self, key: str) -> MutationProposal:
        """Restore the pre-change snapshot where an inverse exists. On-chain
        tightenings have no inverse by contract design: those return
        IRREVERSIBLE with the snapshot attached, never a faked success."""
        proposal = self._get(key)
        if proposal.state not in (APPLIED, VERIFIED, FAILED):
            raise ValueError(
                f"cannot roll back proposal {key!r} in state {proposal.state}"
            )
        rollback_fn = self._surfaces[proposal.surface].rollback
        if rollback_fn is None:
            proposal.state = IRREVERSIBLE
            proposal.history.append((IRREVERSIBLE, {"before": proposal.before}))
            self._audit(proposal, IRREVERSIBLE, {"before": proposal.before})
            return proposal
        try:
            receipt = rollback_fn(proposal.action, dict(proposal.params), dict(proposal.before))
        except Exception as e:  # noqa: BLE001 — same failure contract as apply
            proposal.state = FAILED
            proposal.error = f"rollback failed: {type(e).__name__}: {e}"
            proposal.history.append((FAILED, {"error": proposal.error}))
            self._audit(proposal, FAILED, {"error": proposal.error})
            return proposal
        proposal.receipt = dict(receipt) if isinstance(receipt, dict) else {"result": receipt}
        proposal.state = ROLLED_BACK
        proposal.history.append((ROLLED_BACK, {"receipt": proposal.receipt}))
        self._audit(proposal, ROLLED_BACK, {"receipt": proposal.receipt})
        return proposal

    # -- internals ----------------------------------------------------

    def _get(self, key: str) -> MutationProposal:
        try:
            return self._proposals[key]
        except KeyError:
            raise ValueError(f"unknown mutation key {key!r}") from None

    def _audit(self, proposal: MutationProposal, transition: str, detail: dict) -> None:
        if self._audit_log is None:
            return
        from .audit_trail import PROOF_EXECUTION
        self._audit_log.write(
            f"mutation_{transition}",
            {
                "key": proposal.key,
                "surface": proposal.surface,
                "action": proposal.action,
                "requested_by": proposal.requested_by,
                "state": proposal.state,
                "detail": detail,
            },
            proof=PROOF_EXECUTION,
        )


def default_surfaces(gate, onchain_logger=None, ceilings=None) -> dict[str, SurfaceHandlers]:
    """Wire the real control-plane mutators. gate is a RiskGate;
    onchain_logger is an OnchainLogger or None (chain surfaces then fail
    closed at propose time). ceilings overrides the conservative USD
    defaults below — the operator raises them deliberately, never code."""

    bounds = {"max_position_usd": 5000.0, "cap_usd": 5000.0}
    if ceilings:
        bounds.update(ceilings)

    def _need_chain() -> None:
        if onchain_logger is None:
            raise ValueError("chain surface needs an onchain logger")

    # -- kill switch (both layers, rollback restores the snapshot direction)
    def _ks_read(action: str, params: dict) -> dict:
        return {"active": bool(gate.kill_switch_status()["active"])}

    def _ks_expect(action: str, params: dict) -> dict:
        if action == "activate":
            return {"active": True}
        if action == "deactivate":
            return {"active": False}
        raise ValueError(f"unknown kill_switch action {action!r}")

    def _ks_apply(action: str, params: dict) -> dict:
        reason = params.get("reason", "mutation gate")
        if action == "activate":
            gate.activate_kill_switch(reason)
            if onchain_logger is not None:
                onchain_logger.activate_kill_switch(reason)
            return {"active": True}
        if action == "deactivate":
            gate.deactivate_kill_switch()
            if onchain_logger is not None:
                onchain_logger.deactivate_kill_switch()
            return {"active": False}
        raise ValueError(f"unknown kill_switch action {action!r}")

    def _ks_rollback(action: str, params: dict, before: dict) -> dict:
        if before.get("active"):
            return _ks_apply("activate", params)
        return _ks_apply("deactivate", params)

    # -- on-chain risk params (1e8 fixed point on the wire; tightening-only
    # on-chain, so rollback is None -> IRREVERSIBLE)
    from .audit_logger import _to_fixed_point_1e8  # local: audit_logger never imports this module

    _RP_FIELDS = ("max_position_usd", "max_daily_loss_usd",
                  "max_leverage_bps", "min_confidence_bps")

    def _rp_read(action: str, params: dict) -> dict:
        _need_chain()
        raw = onchain_logger.contract.functions.agentRiskParams(
            onchain_logger.agent_address).call()
        return {
            "max_position_usd": raw[0],
            "max_daily_loss_usd": raw[1],
            "max_leverage_bps": raw[2],
            "min_confidence_bps": raw[3],
        }

    def _rp_expect(action: str, params: dict) -> dict:
        if action != "set":
            raise ValueError(f"unknown risk_params action {action!r}")
        return {
            "max_position_usd": _to_fixed_point_1e8(params["max_position_usd"]),
            "max_daily_loss_usd": _to_fixed_point_1e8(params["max_daily_loss_usd"]),
            "max_leverage_bps": params.get("max_leverage_bps", 50000),
            "min_confidence_bps": params.get("min_confidence_bps", 7000),
        }

    def _rp_apply(action: str, params: dict) -> dict:
        _need_chain()
        tx = onchain_logger.set_risk_params(
            params["max_position_usd"], params["max_daily_loss_usd"],
            params.get("max_leverage_bps", 50000),
            params.get("min_confidence_bps", 7000))
        return {"tx": tx}

    def _rp_ceiling(action: str, params: dict) -> tuple[bool, str]:
        if float(params.get("max_position_usd", 0)) > bounds["max_position_usd"]:
            return (False, f"max_position_usd {params.get('max_position_usd')} "
                           f"exceeds ceiling {bounds['max_position_usd']}")
        return (True, "")

    # -- on-chain per-asset cap (same irreversibility as risk params)
    def _cap_read(action: str, params: dict) -> dict:
        _need_chain()
        from eth_utils import keccak
        asset_hash = keccak(text=params["asset"])
        _, cap = onchain_logger.contract.functions.getAssetDailyStats(
            onchain_logger.agent_address, asset_hash).call()
        return {"cap": cap}

    def _cap_expect(action: str, params: dict) -> dict:
        if action != "set":
            raise ValueError(f"unknown asset_cap action {action!r}")
        return {"cap": _to_fixed_point_1e8(params["cap_usd"])}

    def _cap_apply(action: str, params: dict) -> dict:
        _need_chain()
        tx = onchain_logger.set_asset_cap(params["asset"], params["cap_usd"])
        return {"tx": tx}

    def _cap_ceiling(action: str, params: dict) -> tuple[bool, str]:
        if float(params.get("cap_usd", 0)) > bounds["cap_usd"]:
            return (False, f"cap_usd {params.get('cap_usd')} exceeds "
                           f"ceiling {bounds['cap_usd']}")
        return (True, "")

    return {
        "kill_switch": SurfaceHandlers(
            read=_ks_read, expect=_ks_expect, apply=_ks_apply,
            rollback=_ks_rollback,
            ceiling=lambda action, params: (True, ""),
        ),
        "risk_params": SurfaceHandlers(
            read=_rp_read, expect=_rp_expect, apply=_rp_apply,
            rollback=None,
            ceiling=_rp_ceiling,
        ),
        "asset_cap": SurfaceHandlers(
            read=_cap_read, expect=_cap_expect, apply=_cap_apply,
            rollback=None,
            ceiling=_cap_ceiling,
        ),
    }
