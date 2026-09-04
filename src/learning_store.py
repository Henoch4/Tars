"""Learning-state store (I12) — commits model weights + eval dataset +
curator/ML profiles per cycle with a committed root so the exact model
that priced a cycle is reproducible and auditable.

Connects the ML carry gate to the honest "written first" claim.
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from web3 import Web3
    _HAS_WEB3 = True
except ImportError:
    _HAS_WEB3 = False

from .audit_trail import _keccak, _domain


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _to_jsonable(obj: Any) -> Any:
    """Best-effort JSON-serializable conversion for nested structures."""
    if hasattr(obj, "value") and hasattr(obj, "name"):  # Enum
        return obj.value
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return {k: _to_jsonable(v) for k, v in vars(obj).items()}
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


@dataclass
class LearningState:
    """Complete learning state for one cycle."""
    # Model artifacts (pickled)
    primary_model: bytes | None = None
    meta_model: bytes | None = None
    # Feature engineering state
    feature_names: list[str] = field(default_factory=list)
    # Validation dataset reference
    eval_dataset_hash: str | None = None
    eval_dataset_path: str | None = None
    # Curator/ML profiles active this cycle
    curator_profile: dict | None = None
    ml_profiles: dict = field(default_factory=dict)
    # Validation report that cleared this model
    validation_report: dict | None = None
    # Training metadata
    train_timestamp: float = 0.0
    train_symbols: list[str] = field(default_factory=list)
    n_train_rows: int = 0
    # Cycle linkage
    cycle_id: str = ""
    cycle_timestamp: float = 0.0

    def to_jsonable(self) -> dict:
        """JSON-serializable snapshot (excludes pickled models)."""
        return {
            "cycle_id": self.cycle_id,
            "cycle_timestamp": self.cycle_timestamp,
            "eval_dataset_hash": self.eval_dataset_hash,
            "eval_dataset_path": self.eval_dataset_path,
            "feature_names": self.feature_names,
            "curator_profile": self.curator_profile,
            "ml_profiles": self.ml_profiles,
            "validation_report": self.validation_report,
            "train_timestamp": self.train_timestamp,
            "train_symbols": self.train_symbols,
            "n_train_rows": self.n_train_rows,
            "has_primary_model": self.primary_model is not None,
            "has_meta_model": self.meta_model is not None,
        }

    def model_hashes(self) -> dict:
        """Hashes of the pickled models (for quick comparison)."""
        return {
            "primary": _sha256(self.primary_model).hex() if self.primary_model else None,
            "meta": _sha256(self.meta_model).hex() if self.meta_model else None,
        }

    def canonical_bytes(self) -> bytes:
        """Deterministic bytes for this learning state (for Merkle leaf)."""
        # Include everything that defines this state's identity
        serializable = _to_jsonable({
            "eval_dataset_hash": self.eval_dataset_hash,
            "feature_names": self.feature_names,
            "curator_profile": self.curator_profile,
            "ml_profiles": self.ml_profiles,
            "validation_report": self.validation_report,
            "train_timestamp": self.train_timestamp,
            "train_symbols": self.train_symbols,
            "n_train_rows": self.n_train_rows,
            "model_hashes": self.model_hashes(),
            "cycle_id": self.cycle_id,
        })
        return json.dumps(sorted(serializable.items()), separators=(",", ":")).encode()


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


class LearningStore:
    """Persistent learning-state store with Merkle root per cycle (I12).

    Stores: model weights, eval dataset reference, curator/ML profiles,
    validation report — all versioned by cycle. Each cycle's learning
    state gets a Merkle leaf; the store maintains a Merkle tree over
    cycles so any cycle's exact learning state is verifiable.

    The store is independent of the audit trail (audit_trail.py) but
    uses the same Merkle/hash primitives for consistency.
    """

    def __init__(
        self,
        path: str | Path = "data/learning",
        max_cycles: int = 1000,
    ):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.max_cycles = max_cycles
        self._states: dict[str, LearningState] = {}

    def save(self, state: LearningState) -> str:
        """Persist a learning state for a cycle. Returns the cycle_id."""
        cycle_id = state.cycle_id or f"cycle_{int(state.cycle_timestamp)}"
        state.cycle_id = cycle_id
        state.cycle_timestamp = state.cycle_timestamp or time.time()

        # Write models to separate files (large binaries)
        model_dir = self.path / "models" / cycle_id
        model_dir.mkdir(parents=True, exist_ok=True)
        if state.primary_model:
            (model_dir / "primary.pkl").write_bytes(state.primary_model)
        if state.meta_model:
            (model_dir / "meta.pkl").write_bytes(state.meta_model)

        # Write state JSON (without pickled models)
        state_json = state.to_jsonable()
        state_path = self.path / f"{cycle_id}.json"
        state_path.write_text(json.dumps(state_json, indent=2, default=str))
        self._states[cycle_id] = state
        return cycle_id

    def load(self, cycle_id: str) -> LearningState | None:
        """Load a learning state by cycle ID."""
        if cycle_id in self._states:
            return self._states[cycle_id]
        state_path = self.path / f"{cycle_id}.json"
        if not state_path.exists():
            return None
        data = json.loads(state_path.read_text())
        state = LearningState(
            cycle_id=data.get("cycle_id", ""),
            cycle_timestamp=data.get("cycle_timestamp", 0.0),
            eval_dataset_hash=data.get("eval_dataset_hash"),
            eval_dataset_path=data.get("eval_dataset_path"),
            feature_names=data.get("feature_names", []),
            curator_profile=data.get("curator_profile"),
            ml_profiles=data.get("ml_profiles", {}),
            validation_report=data.get("validation_report"),
            train_timestamp=data.get("train_timestamp", 0.0),
            train_symbols=data.get("train_symbols", []),
            n_train_rows=data.get("n_train_rows", 0),
        )
        # Load pickled models if they exist
        model_dir = self.path / "models" / cycle_id
        primary_path = model_dir / "primary.pkl"
        meta_path = model_dir / "meta.pkl"
        if primary_path.exists():
            state.primary_model = primary_path.read_bytes()
        if meta_path.exists():
            state.meta_model = meta_path.read_bytes()
        self._states[cycle_id] = state
        return state

    def get(self, cycle_id: str) -> LearningState | None:
        """Get a learning state (loads from disk if needed)."""
        if cycle_id in self._states:
            return self._states[cycle_id]
        return self.load(cycle_id)

    def list_cycles(self) -> list[str]:
        """List all cycle IDs in the store."""
        cycles = [p.stem for p in self.path.glob("*.json")]
        return sorted(cycles)

    def compute_root(self, cycle_ids: list[str] | None = None) -> bytes:
        """Merkle root over the canonical bytes of the given cycles.
        If cycle_ids is None, uses all stored cycles in chronological order.
        """
        ids = cycle_ids or self.list_cycles()
        if not ids:
            return b"\x00" * 32
        leaves = []
        for cid in sorted(ids):
            state = self.load(cid)
            if state:
                leaves.append(_sha256(state.canonical_bytes()))
            else:
                leaves.append(b"\x00" * 32)
        return merkle_root([_sha256(l) for l in leaves])


def merkle_root(leaves: list[bytes]) -> bytes:
    """Standard Merkle root (same as audit_trail.merkle_root)."""
    if not leaves:
        return b"\x00" * 32
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [_sha256(level[i] + level[i + 1])
                 for i in range(0, len(level), 2)]
    return level[0]


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _sha256_str(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Import at bottom to avoid circular dependency
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path


import pickle

# --- Backwards-compat helpers for existing pipeline ---
def save_learning_state(
    path: str | Path,
    cycle_id: str,
    primary_model: Any,
    meta_model: Any,
    feature_names: list[str],
    eval_dataset_hash: str,
    eval_dataset_path: str,
    curator_profile: dict | None,
    ml_profiles: dict,
    validation_report: dict | None,
    train_symbols: list[str],
    n_train_rows: int,
) -> str:
    """One-shot save (compatible with pipeline.py's output)."""
    store = LearningStore(path=Path(path).parent)
    state = LearningState(
        cycle_id=cycle_id,
        primary_model=pickle.dumps(primary_model) if primary_model else None,
        meta_model=pickle.dumps(meta_model) if meta_model else None,
        feature_names=feature_names,
        eval_dataset_hash=eval_dataset_hash,
        eval_dataset_path=eval_dataset_path,
        curator_profile=curator_profile,
        ml_profiles=ml_profiles,
        validation_report=validation_report,
        train_timestamp=time.time(),
        train_symbols=train_symbols,
        n_train_rows=n_train_rows,
    )
    return store.save(state)


def load_learning_state(path: str | Path, cycle_id: str):
    """Load a learning state by cycle ID."""
    store = LearningStore(path=Path(path))
    return store.load(cycle_id)