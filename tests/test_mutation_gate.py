"""TODO 3.8 (CA7): mutation-gate lifecycle for production writes.

Every production mutation must go preview -> approval -> apply -> verify,
with an idempotency key, an audit record per transition, and a rollback
where one exists. These tests pin each stage against fakes (kill-switch
surface over a fake gate, chain surfaces over a fake logger) plus one
real AuditLog for the audit leg.
"""
import pytest

from src.audit_trail import AuditLog
from src.mutation_gate import (
    APPROVED,
    APPLIED,
    FAILED,
    IRREVERSIBLE,
    PROPOSED,
    REJECTED,
    ROLLED_BACK,
    VERIFIED,
    MutationGate,
    SurfaceHandlers,
    default_surfaces,
)


class FakeGate:
    def __init__(self):
        self.active = False
        self.calls = []

    def kill_switch_status(self):
        return {"active": self.active}

    def activate_kill_switch(self, reason=""):
        self.calls.append(("activate", reason))
        self.active = True

    def deactivate_kill_switch(self):
        self.calls.append(("deactivate",))
        self.active = False


class FakeChain:
    """Minimal OnchainLogger shape: canned reads, recording writes."""

    def __init__(self):
        self.agent_address = "0x" + "ab" * 20
        self.params = {
            "max_position_usd": 5000_00000000,
            "max_daily_loss_usd": 500_00000000,
            "max_leverage_bps": 50000,
            "min_confidence_bps": 7000,
        }
        self.caps = {}
        self.calls = []

    class _Fns:
        def __init__(self, outer):
            self._o = outer

        def agentRiskParams(self, addr):
            o = self._o

            class Call:
                def call(self):
                    p = o.params
                    return (p["max_position_usd"], p["max_daily_loss_usd"],
                            p["max_leverage_bps"], p["min_confidence_bps"])
            return Call()

        def getAssetDailyStats(self, addr, asset_hash):
            o = self._o

            class Call:
                def call(self):
                    return (0, o.caps.get(bytes(asset_hash).hex(), 0))
            return Call()

    @property
    def contract(self):
        return type("C", (), {"functions": FakeChain._Fns(self)})()

    def set_risk_params(self, pos, loss, lev=50000, conf=7000):
        from src.audit_logger import _to_fixed_point_1e8
        self.calls.append(("set_risk_params", pos, loss, lev, conf))
        self.params = {
            "max_position_usd": _to_fixed_point_1e8(pos),
            "max_daily_loss_usd": _to_fixed_point_1e8(loss),
            "max_leverage_bps": lev,
            "min_confidence_bps": conf,
        }
        return "0xtx1"

    def set_asset_cap(self, asset, cap_usd):
        from src.audit_logger import _to_fixed_point_1e8
        from web3 import Web
        self.calls.append(("set_asset_cap", asset, cap_usd))
        self.caps[Web.keccak(text=asset).hex()] = _to_fixed_point_1e8(cap_usd)
        return "0xtx2"

    def activate_kill_switch(self, reason=""):
        self.calls.append(("chain_activate", reason))

    def deactivate_kill_switch(self):
        self.calls.append(("chain_deactivate",))


def _gate(**kw):
    fakes = kw.pop("fakes", None)
    gate = FakeGate()
    chain = FakeChain()
    mg = MutationGate(default_surfaces(gate, chain), **kw)
    return mg, gate, chain


def _full_cycle(mg, key, surface, action, params, by="op", approver="owner"):
    mg.propose(key, surface, action, params, by)
    mg.approve(key, approver)
    return mg.apply(key)


def test_preview_shows_diff_without_changing_state():
    mg, gate, _ = _gate()
    mg.propose("k1", "kill_switch", "activate", {"reason": "drill"}, "op")
    diff = mg.preview("k1")
    assert diff["before"] == {"active": False}
    assert diff["after"] == {"active": True}
    assert gate.active is False  # preview applies nothing
    assert gate.calls == []


def test_approval_within_ceilings_and_over_ceiling_rejected():
    mg, _, _ = _gate()
    mg.propose("k1", "risk_params", "set",
               {"max_position_usd": 1000.0, "max_daily_loss_usd": 100.0}, "op")
    p = mg.approve("k1", "owner")
    assert p.state == APPROVED

    mg.propose("k2", "risk_params", "set",
               {"max_position_usd": 6000.0, "max_daily_loss_usd": 100.0}, "op")
    p2 = mg.approve("k2", "owner")
    assert p2.state == REJECTED
    assert "ceiling" in p2.error


def test_apply_executes_once_repeated_apply_replays_receipt():
    mg, gate, _ = _gate()
    _full_cycle(mg, "k1", "kill_switch", "activate", {"reason": "drill"})
    assert gate.active is True
    n_calls = len(gate.calls)
    p = mg.apply("k1")  # replay: no re-execution
    assert p.state == VERIFIED
    assert len(gate.calls) == n_calls
    assert p.history[-1][0] == "apply_replayed"


def test_apply_without_approval_raises():
    mg, _, _ = _gate()
    mg.propose("k1", "kill_switch", "activate", {}, "op")
    with pytest.raises(ValueError):
        mg.apply("k1")


def test_verify_confirms_applied_state():
    mg, gate, _ = _gate()
    p = _full_cycle(mg, "k1", "kill_switch", "activate", {"reason": "drill"})
    assert p.state == VERIFIED
    assert gate.active is True


def test_failure_marks_failed_and_audits(tmp_path):
    log = AuditLog(path=str(tmp_path / "audit.jsonl"))

    def boom(action, params):
        raise RuntimeError("chain down")

    mg = MutationGate(
        {"brittle": SurfaceHandlers(
            read=lambda a, p: {}, expect=lambda a, p: {},
            apply=boom, rollback=None,
            ceiling=lambda a, p: (True, ""))},
        audit_log=log, approval_required=False,
    )
    mg.propose("k1", "brittle", "do", {}, "op")
    p = mg.apply("k1")
    assert p.state == FAILED
    assert "chain down" in p.error
    lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
    assert any("mutation_failed" in line for line in lines)


def test_rollback_restores_kill_switch_snapshot():
    mg, gate, _ = _gate()
    gate.active = True  # before-snapshot: halted
    p = _full_cycle(mg, "k1", "kill_switch", "deactivate", {})
    assert gate.active is False
    p = mg.rollback("k1")
    assert p.state == ROLLED_BACK
    assert gate.active is True  # snapshot restored on both layers


def test_rollback_of_tightening_is_explicitly_irreversible():
    mg, _, chain = _gate()
    p = _full_cycle(mg, "k1", "risk_params", "set",
                    {"max_position_usd": 1000.0, "max_daily_loss_usd": 100.0})
    assert p.state == VERIFIED
    p = mg.rollback("k1")
    assert p.state == IRREVERSIBLE  # chain forbids loosening: no faked success
    assert p.before["max_position_usd"] == 5000_00000000  # snapshot kept


def test_duplicate_propose_returns_same_proposal():
    mg, _, _ = _gate()
    p1 = mg.propose("k1", "kill_switch", "activate", {}, "op")
    p2 = mg.propose("k1", "kill_switch", "deactivate", {}, "op2")
    assert p2 is p1
    assert p1.action == "activate"  # first intent wins


def test_unknown_surface_key_action_rejected():
    mg, _, _ = _gate()
    with pytest.raises(ValueError):
        mg.propose("k1", "nope", "set", {}, "op")
    with pytest.raises(ValueError):
        mg.preview("missing")
    with pytest.raises(ValueError):
        mg.rollback("missing")


def test_self_approval_recorded_not_hidden():
    mg, _, _ = _gate()
    mg.propose("k1", "kill_switch", "activate", {}, "op")
    p = mg.approve("k1", "op")
    assert p.state == APPROVED
    assert p.self_approved is True


def test_full_lifecycle_audits_every_transition(tmp_path):
    log = AuditLog(path=str(tmp_path / "audit.jsonl"))
    mg, _, _ = _gate(audit_log=log)
    _full_cycle(mg, "k1", "kill_switch", "activate", {"reason": "drill"})
    mg.rollback("k1")
    lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
    kinds = [line for line in lines if "mutation_" in line]
    assert len(kinds) >= 5  # proposed, approved, applied, verified, rolled_back
    assert any("mutation_rolled_back" in line for line in lines)
