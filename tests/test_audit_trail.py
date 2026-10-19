"""Unit tests for src/audit_trail.py — the local append-only audit log.

No deps, no network. Run: pytest tests/test_audit_trail.py -v
"""
import json
import re
from enum import Enum
from pathlib import Path

import pytest

from src.audit_trail import (
    AuditLog,
    PROOF_EVIDENCE,
    _jsonable,
    merkle_proof,
    merkle_root,
    verify_membership,
    _canonical_record,
    _keccak,
)


class FakeEvt(Enum):
    REJECTED = "rejected"


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit_log.jsonl"


def test_write_creates_append_only_jsonl(audit_path: Path):
    log = AuditLog(audit_path)
    log.write("risk_rejection", {"reason": "STALE_PRICE", "order": {"symbol": "BTC"}})
    log.write("curator_switch", {"profile": "defensive"})

    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        assert json.loads(line)  # every line is valid JSON


def test_record_shape(audit_path: Path):
    log = AuditLog(audit_path)
    log.write("risk_rejection", {"reason": "STALE_PRICE"})
    record = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert set(record) == {"ts", "event_type", "proof", "payload"}
    assert record["event_type"] == "risk_rejection"
    assert record["proof"] == PROOF_EVIDENCE
    assert record["payload"] == {"reason": "STALE_PRICE"}
    assert isinstance(record["ts"], (int, float))


def test_append_only_never_rewrites(audit_path: Path):
    log = AuditLog(audit_path)
    log.write("one", {"n": 1})
    first_lines = audit_path.read_text(encoding="utf-8").strip().splitlines()

    log.write("two", {"n": 2})
    all_lines = audit_path.read_text(encoding="utf-8").strip().splitlines()

    assert all_lines[: len(first_lines)] == first_lines
    assert len(all_lines) == len(first_lines) + 1


def test_jsonable_handles_enum(audit_path: Path):
    log = AuditLog(audit_path)
    log.write("risk_rejection", {"reason": FakeEvt.REJECTED})
    record = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["payload"]["reason"] == "rejected"


def test_jsonable_recurses_dataclass(audit_path: Path):
    from dataclasses import dataclass

    @dataclass
    class Order:
        cl_ord_id: str

    log = AuditLog(audit_path)
    log.write("order_submitted", {"order": Order("abc-1")})
    record = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["payload"]["order"] == {"cl_ord_id": "abc-1"}


def test_missing_parent_dir_raises_for_failure_isolation(audit_path: Path):
    # Caller must create directories; failing loudly beats silently logging nowhere.
    log = AuditLog(audit_path.parent / "nope" / "audit.jsonl")
    with pytest.raises(FileNotFoundError):
        log.write("risk_rejection", {"reason": "STALE_PRICE"})


def test_iso_timestamp_line_does_not_corrupt_json(audit_path: Path):
    log = AuditLog(audit_path)
    log.write("with_str_value", {"note": "funding rate NaN -> HARD BLOCK"})
    raw = audit_path.read_text(encoding="utf-8")
    assert not re.search(r"\n\s*\n", raw)  # no empty/partial lines
    json.loads(raw.strip().splitlines()[0])


def _run_cycle(log: AuditLog, cid: str, n: int = 2):
    log.begin_cycle(cid)
    for i in range(n):
        log.write("observation", {"i": i})
    return log.seal_cycle()


def test_seal_root_verifies_membership(audit_path: Path):
    log = AuditLog(audit_path)
    seal = _run_cycle(log, "c1", 3)
    assert seal["count"] == 3
    assert seal["cycle_id"] == "c1"
    leaves = [_keccak(_canonical_record(r)) for r in log._cycle_records]
    assert leaves == []  # buffer cleared by seal; rebuild from file below
    recs = [json.loads(ln) for ln in
            audit_path.read_text(encoding="utf-8").splitlines()
            if '"seal"' not in ln]
    leaves = [_keccak(_canonical_record(r)) for r in recs]
    assert merkle_root(leaves).hex() == seal["root"]
    # membership proof built by the module verifies against the sealed root;
    # a forged leaf does not.
    root = bytes.fromhex(seal["root"])
    proof = merkle_proof(leaves, 0)
    assert verify_membership(leaves[0], proof, root, 0) is True
    assert verify_membership(_keccak(b"forged"), proof, root, 0) is False


def test_rolloff_keeps_seals_and_recent_drops_old(audit_path: Path):
    log = AuditLog(audit_path)
    for cid in ("c1", "c2", "c3"):
        _run_cycle(log, cid, 2)
    out = log.rolloff(keep_cycles=1)
    assert out["seals"] == 3  # seals never dropped
    assert out["dropped"] == 4  # c1+c2 details (2 each)
    recs = [json.loads(ln) for ln in
            audit_path.read_text(encoding="utf-8").splitlines()]
    kinds = [r["event_type"] for r in recs]
    assert kinds.count("seal") == 3
    assert kinds.count("observation") == 2
    assert {r.get("cycle_id") for r in recs
            if r["event_type"] == "observation"} == {"c3"}


def test_rolloff_keeps_unsealed_tail(audit_path: Path):
    log = AuditLog(audit_path)
    _run_cycle(log, "c1", 1)
    log.begin_cycle("c2-tail")
    log.write("observation", {"i": 0})
    out = log.rolloff(keep_cycles=1)
    recs = [json.loads(ln) for ln in
            audit_path.read_text(encoding="utf-8").splitlines()]
    assert out["dropped"] == 1  # only sealed c1 detail
    assert any(r.get("cycle_id") == "c2-tail" for r in recs)


def test_rolloff_rejects_zero_keep(audit_path: Path):
    import pytest as _pytest
    log = AuditLog(audit_path)
    with _pytest.raises(ValueError):
        log.rolloff(keep_cycles=0)


def test_rolloff_missing_file_is_empty_result(tmp_path: Path):
    log = AuditLog(tmp_path / "absent.jsonl")
    assert log.rolloff() == {"kept": 0, "dropped": 0, "seals": 0}