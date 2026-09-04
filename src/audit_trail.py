"""
Immutable, append-only local audit trail.

Every governance decision gets logged -- not just fills: risk-gate rejections,
curator switches, integrity blocks. JSONL keeps it trivially append-only and
greppable for reconciliation. This complements the on-chain audit logger:
the chain proves the decision was signed; this file records the reasoning and
the exact inputs that produced it.

Ported from the sibling `trading_system` MVP (audit/audit_log.py).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

_HASH_DOMAIN = None  # resolved once, lazily — see _domain()


def _domain() -> str:
    """Resolved hash domain for this process (keccak when web3 is present,
    sha3-256 otherwise). Computed once; recorded in every seal so verifiers
    never mix domains."""
    global _HASH_DOMAIN
    if _HASH_DOMAIN is None:
        try:
            from web3 import Web3  # noqa: F401
            _HASH_DOMAIN = "keccak256"
        except ImportError:
            _HASH_DOMAIN = "sha3_256-fallback"
    return _HASH_DOMAIN


# ─── Proof taxonomy (I11) ───
# What KIND of fact a record is, so auditors and verifiers can separate
# them instead of replaying one untyped log:
PROOF_DECISION = "decision"    # what was decided (signals, gate verdicts)
PROOF_EXECUTION = "execution"  # what was done (fills, unwinds, settles)
PROOF_EVIDENCE = "evidence"    # what was observed (market snapshots, blocks)
PROOF_EVOLUTION = "evolution"  # what the model/curator updated (learning)
PROOF_KINDS = frozenset({PROOF_DECISION, PROOF_EXECUTION, PROOF_EVIDENCE,
                         PROOF_EVOLUTION})


def _keccak(data: bytes) -> bytes:
    """keccak256, matching the on-chain hash domain (for future seal anchoring)."""
    try:
        from web3 import Web3
        return Web3.keccak(data)
    except ImportError:
        # Fallback for environments without web3: NOT consensus-compatible
        # with the chain, but deterministic within a process. The seal
        # records which domain it used so verifiers never mix them.
        return hashlib.sha3_256(data).digest()


def _canonical_record(record: dict) -> bytes:
    """Deterministic byte encoding of one audit record (leaf preimage)."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")


def merkle_root(leaves: list[bytes]) -> bytes:
    """keccak Merkle root over pre-hashed leaves. Empty tree -> 32 zero bytes
    (NOT a hash of nothing — distinguishable from any real seal)."""
    if not leaves:
        return b"\x00" * 32
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [_keccak(level[i] + level[i + 1])
                 for i in range(0, len(level), 2)]
    return level[0]


def merkle_proof(leaves: list[bytes], index: int) -> list[str]:
    """Membership proof for leaves[index]: sibling hashes from leaf level up,
    ordered leaf-to-root. Direction is reconstructed by the verifier from the
    claimed index — a proof mis-stating the index fails, and a proof carrying
    its own direction flags would be self-attesting (the bug the first cut
    had: index was computed then ignored)."""
    if not 0 <= index < len(leaves):
        raise ValueError(f"index {index} out of range for {len(leaves)} leaves")
    proof: list[str] = []
    level = list(leaves)
    idx = index
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        sibling = idx ^ 1
        proof.append(level[sibling].hex())
        idx //= 2
        level = [_keccak(level[i] + level[i + 1])
                 for i in range(0, len(level), 2)]
    return proof


def verify_membership(leaf: bytes, proof: list[str], root: bytes,
                      index: int) -> bool:
    """Recompute the root from a leaf + proof + claimed index. Direction at
    each level is derived from the index bits (even = leaf on the left) —
    any tampering of leaf, index, or sibling hash fails closed."""
    try:
        running = leaf
        idx = int(index)
        for step in proof:
            sib = bytes.fromhex(step)
            if idx % 2 == 0:
                running = _keccak(running + sib)
            else:
                running = _keccak(sib + running)
            idx //= 2
        return running == root
    except (ValueError, AttributeError, TypeError):
        return False


class AuditLog:
    # Two-tier retention (I5): the JSONL file is the cheap working buffer
    # (every observation lands here); the on-chain logDecision stays the
    # permanent record for real decisions. Transient observations are NEVER
    # promoted on-chain by this class — sealing only commits a Merkle root
    # over what is already here, so the seal cannot widen on-chain writes.
    def __init__(self, path: str | Path = "audit_log.jsonl"):
        self.path = Path(path)
        self._cycle_id: str | None = None
        self._cycle_records: list[dict] = []

    def write(self, event_type: str, payload: dict,
              proof: str = PROOF_EVIDENCE):
        """Append one record. Append-only: readers must not rewrite the file.

        proof names the fact kind (I11 taxonomy); defaults to evidence so
        existing callers are unchanged.
        """
        if proof not in PROOF_KINDS:
            raise ValueError(f"unknown proof kind {proof!r} "
                             f"(want one of {sorted(PROOF_KINDS)})")
        record = {
            "ts": time.time(),
            "event_type": event_type,
            "proof": proof,
            "payload": _jsonable(payload),
        }
        if self._cycle_id is not None:
            record["cycle_id"] = self._cycle_id
            self._cycle_records.append(record)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        return record

    def begin_cycle(self, cycle_id: str) -> None:
        """Open a seal window: subsequent writes buffer for the seal."""
        self._cycle_id = cycle_id
        self._cycle_records = []

    def seal_cycle(self) -> dict:
        """Seal the buffered cycle records (I5+I11).

        Builds a Merkle tree over the canonical encodings, writes a `seal`
        record to the JSONL log, and returns it. The buffer is memory-only:
        a crash loses the unsealed window (working tier), never the file.
        An empty window seals to the zero root — distinguishable from any
        real seal, and honest about there being nothing to commit.
        """
        leaves = [_keccak(_canonical_record(r)) for r in self._cycle_records]
        root = merkle_root(leaves)
        seal = {
            "ts": time.time(),
            "event_type": "seal",
            "proof": PROOF_EVIDENCE,
            "cycle_id": self._cycle_id,
            "root": root.hex(),
            "hash_domain": _domain(),
            "count": len(self._cycle_records),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(seal, default=str) + "\n")
        self._cycle_id = None
        self._cycle_records = []
        return seal


def _jsonable(obj):
    """Best-effort conversion of dataclasses/enums nested in payloads.

    Enum must be checked before the generic object branch: Enum members have
    a ``__dict__`` too, and converting that recurses into the member map.
    """
    if hasattr(obj, "value") and hasattr(obj, "name"):  # Enum
        return obj.value
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return {k: _jsonable(v) for k, v in vars(obj).items()}
    return obj