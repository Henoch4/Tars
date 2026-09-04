"""I5+I11 regression: proof taxonomy, Merkle seal, membership proofs.

Pins: taxonomy kinds (+ invalid rejected, evidence default), deterministic
roots, empty-window zero root, proofs verify, any tampering fails closed,
seal roundtrip through AuditLog, and agent cycle wiring.
"""
import json
import os
import tempfile

import pytest

from src.audit_trail import (
    PROOF_DECISION,
    PROOF_EVIDENCE,
    PROOF_EVOLUTION,
    PROOF_EXECUTION,
    AuditLog,
    merkle_proof,
    merkle_root,
    verify_membership,
    _canonical_record,
    _keccak,
)


def _log():
    d = tempfile.mkdtemp(prefix="audit_seal_")
    return AuditLog(os.path.join(d, "audit.jsonl"))


class TestTaxonomy:
    def test_kinds_accepted(self):
        log = _log()
        for kind in (PROOF_DECISION, PROOF_EXECUTION, PROOF_EVIDENCE,
                     PROOF_EVOLUTION):
            rec = log.write("x", {"a": 1}, proof=kind)
            assert rec["proof"] == kind

    def test_default_is_evidence(self):
        log = _log()
        assert log.write("x", {})["proof"] == PROOF_EVIDENCE

    def test_unknown_kind_rejected(self):
        log = _log()
        with pytest.raises(ValueError):
            log.write("x", {}, proof="vibes")


class TestMerkle:
    def _leaves(self, n=4):
        return [_keccak(f"leaf-{i}".encode()) for i in range(n)]

    def test_root_deterministic(self):
        assert (merkle_root(self._leaves()).hex()
                == merkle_root(self._leaves()).hex())

    def test_empty_tree_is_zero_root(self):
        assert merkle_root([]) == b"\x00" * 32

    def test_single_leaf_root_is_leaf(self):
        (leaf,) = self._leaves(1)
        assert merkle_root([leaf]) == leaf

    def test_proofs_verify(self):
        leaves = self._leaves(5)
        root = merkle_root(leaves)
        for i in range(5):
            assert verify_membership(leaves[i], merkle_proof(leaves, i),
                                     root, i) is True

    def test_tampered_leaf_fails(self):
        leaves = self._leaves(4)
        root = merkle_root(leaves)
        assert verify_membership(_keccak(b"evil"),
                                 merkle_proof(leaves, 0), root, 0) is False

    def test_wrong_index_fails(self):
        # Direction is derived from the claimed index — verifying leaf 3's
        # proof at index 2 flips a level's pair order and must not land
        # on the root.
        leaves = self._leaves(5)
        root = merkle_root(leaves)
        assert verify_membership(leaves[3], merkle_proof(leaves, 3),
                                 root, 2) is False
        assert verify_membership(leaves[0], merkle_proof(leaves, 0),
                                 root, 1) is False

    def test_proof_for_wrong_leaf_fails(self):
        leaves = self._leaves(4)
        root = merkle_root(leaves)
        # Proof built for leaf 0 must not verify leaf 1 at index 0.
        assert verify_membership(leaves[1], merkle_proof(leaves, 0),
                                 root, 0) is False

    def test_malformed_proof_fails_closed(self):
        leaves = self._leaves(2)
        root = merkle_root(leaves)
        assert verify_membership(leaves[0], ["not-hex-chars"], root, 0) is False
        assert verify_membership(leaves[0], None, root, 0) is False
        assert verify_membership(leaves[0], 7, root, 0) is False

    def test_proof_index_out_of_range_raises(self):
        with pytest.raises(ValueError):
            merkle_proof(self._leaves(2), 7)


class TestSealRoundtrip:
    def test_seal_commits_buffered_records(self):
        log = _log()
        log.begin_cycle("c1")
        log.write("risk_rejection", {"code": "X"}, proof=PROOF_DECISION)
        log.write("fill", {"px": 1}, proof=PROOF_EXECUTION)
        seal = log.seal_cycle()
        assert seal["cycle_id"] == "c1"
        assert seal["count"] == 2
        assert len(bytes.fromhex(seal["root"])) == 32
        assert seal["root"] != "00" * 32
        assert seal["hash_domain"] == "keccak256"

    def test_seal_record_in_file(self):
        log = _log()
        log.begin_cycle("c9")
        log.write("x", {})
        seal = log.seal_cycle()
        lines = [json.loads(line) for line in open(log.path)]
        assert lines[-1]["event_type"] == "seal"
        assert lines[-1]["root"] == seal["root"]

    def test_empty_window_seals_zero(self):
        log = _log()
        log.begin_cycle("empty")
        seal = log.seal_cycle()
        assert seal["count"] == 0
        assert seal["root"] == "00" * 32  # 32 zero bytes, 64 hex chars

    def test_buffer_clears_between_cycles(self):
        log = _log()
        log.begin_cycle("c1")
        log.write("x", {})
        first = log.seal_cycle()
        log.begin_cycle("c2")
        log.write("x", {})
        log.write("x", {})
        second = log.seal_cycle()
        assert first["root"] != second["root"]
        assert second["count"] == 2

    def test_membership_against_sealed_cycle(self):
        log = _log()
        log.begin_cycle("c1")
        recs = [log.write("d", {"i": i}, proof=PROOF_DECISION)
                for i in range(3)]
        seal = log.seal_cycle()
        leaves = [_keccak(_canonical_record(r)) for r in recs]
        assert merkle_root(leaves).hex() == seal["root"]
        root = bytes.fromhex(seal["root"])
        for i, leaf in enumerate(leaves):
            assert verify_membership(leaf, merkle_proof(leaves, i),
                                     root, i) is True


@pytest.mark.asyncio
async def test_agent_cycle_carries_seal():
    """Agent opens the seal window per cycle and attaches the seal record."""
    from src.agent import AutonomousTradingAgent
    from src.execution import RiskGate

    class _StubCli:
        async def run(self, *args, **kwargs):
            cmd = args[:1]
            if cmd == ("market",) and "trades" in args:
                return {"data": [{"px": "100", "sz": "1"} for _ in range(30)]}
            if cmd == ("market",) and "funding-rate" in args:
                return {"data": [{"fundingRate": "0.00001"}]}
            return {"data": []}

    gate = RiskGate(
        max_position_usd=5000,
        allowed_assets=["BTC-USDT-SWAP"],
        allowed_companions=[],
    )
    d = tempfile.mkdtemp(prefix="audit_seal_agent_")
    log = AuditLog(os.path.join(d, "audit.jsonl"))
    agent = AutonomousTradingAgent(okx_cli=_StubCli(), risk_gate=gate,
                                   dry_run=True, audit_log=log)
    result = await agent.run_trading_cycle(["BTC-USDT-SWAP"])
    assert result.seal is not None
    assert result.seal["cycle_id"] == result.cycle_id
    assert result.seal["count"] >= 0
