"""Data-drawer persistence — the durable per-training artifact store (Free1).

Ports the freqtrade `freqai/data_drawer.py` SHAPE (pattern only, no code
copied): per-training artifacts live in timestamped sub-runs under
`models/<identifier>/sub-train-<PAIR>_<TIMESTAMP>/`, with a
`pair_dictionary.json` index pointing at the newest run per pair.

What this solves: the repo had ONE flat training sidecar
(`ml/train_carry.py` → `models/carry_quantile/meta.json`). A restarted bot
could not resume with the last fitted artifacts, and there was no retention
discipline (stale runs pile up or get deleted by hand). The drawer is the
additive answer — the flat sidecar loader (`ml/carry_inference.py`) is
untouched and keeps working; wiring the trainer over to the drawer is a
later, deliberate change, not this one.

Layout:

    <root>/<identifier>/sub-train-<PAIR>_<TIMESTAMP_MS>/
        <artifact-name>          # caller-serialized binaries (joblib etc.)
        run_metadata.json        # drawer-written entry + artifact digests
    <root>/<identifier>/pair_dictionary.json
        pair → {subdirectory, trained_timestamp, artifact_names, digests,
                metadata}        # authoritative pointer at the newest run

Semantics:
  - **Latest wins in the index; history is the run dirs.** A new `save` at
    a fresh timestamp rewrites the pair's index entry and LEAVES the old
    run dir on disk — retention is `purge`'s explicit decision, never a
    side effect of saving.
  - **Atomic:** every save writes into a temp run dir and `os.replace`s it
    into place; the index is rewritten the same way. A crash mid-save
    leaves either the old or the new state, never a torn file.
  - **Fail-closed:** unknown identifier/pair → `DrawerEmpty` (never an
    implicit creation); re-saving an existing run timestamp →
    `DrawerConflict` (deliberate re-run needs a fresh timestamp); corrupt
    index / missing artifact / digest mismatch / torn directory-vs-index
    disagreement → `DrawerCorrupt`. No silent defaults anywhere.
  - **Content-addressed:** each artifact's sha256 is recorded at save and
    verified on every `latest()` read — a truncated or bit-rotted model
    file is detected, not loaded.
  - **Retention is explicit:** `purge` removes whole run dirs ranked
    OLDEST-first beyond `keep` (the newest run is never deleted), requires
    the index/disks to agree before touching anything, and `keep < 1` is
    refused. Cross-pair artifacts and foreign dirs are never touched.

Pure IO discipline: stdlib only (os/json/hashlib/re/shutil), no internal
clock (callers pass `trained_timestamp`), no ML deps, `models/` usable as
the root without touching any existing flat sidecar.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

RUN_PREFIX = "sub-train-"
# Pair charset is deliberately the same family the run filename regex can
# parse back out; extended chars = future deliberate change, tests pin it.
PAIR_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,64}$")
RUN_RE = re.compile(
    r"^" + re.escape(RUN_PREFIX) + r"(?P<pair>[A-Za-z0-9_\-\.]+)_(?P<ts>\d{13})$"
)
RUN_METADATA_FILE = "run_metadata.json"
INDEX_FILE = "pair_dictionary.json"


class DrawerError(Exception):
    """Base — every drawer failure is typed."""


class DrawerEmpty(DrawerError):
    """Nothing stored there (yet). NEVER an implicit creation."""


class DrawerConflict(DrawerError):
    """A run already exists for this (identifier, pair, timestamp)."""


class DrawerCorrupt(DrawerError):
    """Readable but wrong: bad index, missing artifact, digest mismatch."""


def _pair_check(pair: str) -> str:
    if not isinstance(pair, str) or not PAIR_RE.match(pair or ""):
        raise ValueError(f"invalid pair {pair!r}")
    return pair


def _ts_check(trained_timestamp: object) -> int:
    if isinstance(trained_timestamp, bool) or not isinstance(
        trained_timestamp, int
    ):
        raise TypeError(
            "trained_timestamp must be int ms epoch, got "
            f"{type(trained_timestamp).__name__}"
        )
    if trained_timestamp < 0:
        raise ValueError("trained_timestamp must be non-negative")
    return trained_timestamp


class DataDrawer:
    """Durable artifact store rooted at `root` (default `models/`)."""

    def __init__(self, root: str | os.PathLike[str] = "models"):
        self.root = Path(root)

    # -- internal paths / index ---------------------------------------------

    def _identifier_dir(self, identifier: str) -> Path:
        if not isinstance(identifier, str) or not IDENTIFIER_RE.match(identifier or ""):
            raise ValueError(f"invalid identifier {identifier!r}")
        return self.root / identifier

    def _index_path(self, identifier: str) -> Path:
        return self._identifier_dir(identifier) / INDEX_FILE

    def _load_index(self, identifier: str) -> dict:
        index_path = self._index_path(identifier)
        if not index_path.exists():
            raise DrawerEmpty(f"no drawer index for {identifier!r}")
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            raise DrawerCorrupt(f"unreadable index for {identifier!r}: {e}") from e
        if not isinstance(index, dict) or not all(
            isinstance(k, str) and isinstance(v, dict) for k, v in index.items()
        ):
            raise DrawerCorrupt(f"index for {identifier!r} has the wrong shape")
        return index

    def _write_index(self, identifier: str, index: Mapping[str, Any]) -> None:
        tmp = self._index_path(identifier).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._index_path(identifier))

    def _run_dir(self, identifier: str, pair: str, ts: int) -> Path:
        return (
            self._identifier_dir(identifier)
            / f"{RUN_PREFIX}{pair}_{ts:013d}"
        )

    # -- write --------------------------------------------------------------

    def save(
        self,
        *,
        identifier: str,
        pair: str,
        trained_timestamp: int,
        artifacts: Mapping[str, bytes],
        metadata: Mapping[str, Any],
    ) -> dict:
        """Store one training run atomically; latest-wins in the index.
        Old run dirs stay on disk (retention belongs to `purge`). Returns
        the new index entry."""
        pair = _pair_check(pair)
        ts = _ts_check(trained_timestamp)
        if not isinstance(artifacts, Mapping) or not artifacts:
            raise ValueError("artifacts must be a non-empty mapping of bytes")
        if "metadata" in artifacts:
            raise ValueError(
                "artifact name 'metadata' is reserved (index entry field)"
            )
        for name in artifacts:
            if not isinstance(name, str) or not NAME_RE.match(name or ""):
                raise ValueError(f"invalid artifact name {name!r}")
            data = artifacts[name]
            if not isinstance(data, (bytes, bytearray)):
                raise TypeError(f"artifact {name!r} must be bytes")
        self._identifier_dir(identifier)  # validates identifier

        index = None
        try:
            index = self._load_index(identifier)
        except DrawerEmpty:
            index = {}
        run_dir = self._run_dir(identifier, pair, ts)
        if run_dir.exists():
            raise DrawerConflict(
                f"run already exists for {identifier!r}/{pair} at {ts} — "
                f"re-run with a fresh timestamp (retention is purge's job)"
            )

        entry = {
            "subdirectory": run_dir.name,
            "trained_timestamp": ts,
            "artifact_names": sorted(artifacts),
            "digests": {
                name: hashlib.sha256(bytes(data)).hexdigest()
                for name, data in artifacts.items()
            },
            "metadata": dict(metadata),
        }

        tmp_dir = self._identifier_dir(identifier) / (run_dir.name + ".tmp")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        try:
            tmp_dir.mkdir(parents=True)
            for name in sorted(artifacts):
                (tmp_dir / name).write_bytes(bytes(artifacts[name]))
            (tmp_dir / RUN_METADATA_FILE).write_text(
                json.dumps(entry, indent=2, sort_keys=True), encoding="utf-8"
            )
            os.replace(tmp_dir, run_dir)
            index[pair] = entry
            self._write_index(identifier, index)
        except Exception:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        return entry

    # -- read ---------------------------------------------------------------

    def latest(self, *, identifier: str, pair: str) -> dict:
        """Load the newest run's entry, fully verified. Fail-closed."""
        pair = _pair_check(pair)
        index = self._load_index(identifier)
        entry = index.get(pair)
        if entry is None:
            raise DrawerEmpty(f"nothing stored for {identifier!r}/{pair}")
        return self._verified(identifier, entry)

    def _verified(self, identifier: str, entry: Mapping[str, Any]) -> dict:
        run_dir = self._identifier_dir(identifier) / str(entry["subdirectory"])
        if not run_dir.is_dir():
            raise DrawerCorrupt(f"index points at missing run dir {run_dir}")
        for name in entry["artifact_names"]:
            path = run_dir / name
            if not path.is_file():
                raise DrawerCorrupt(f"missing artifact {path}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != entry["digests"].get(name):
                raise DrawerCorrupt(f"digest mismatch for {path}")
        meta_path = run_dir / RUN_METADATA_FILE
        try:
            disk_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            raise DrawerCorrupt(f"unreadable {meta_path}: {e}") from e
        if disk_meta.get("digests") != entry.get("digests"):
            raise DrawerCorrupt(
                f"{meta_path} disagrees with the index (torn write?)"
            )
        return dict(entry)

    def list_runs(self, *, identifier: str, pair: str) -> list[dict]:
        """Full history for the pair, NEWEST first. Each run dir's
        `run_metadata.json` is parsed (DrawerCorrupt on unreadable/shapeless);
        full integrity verification is `latest()`'s job, not this scan's."""
        pair = _pair_check(pair)
        index = self._load_index(identifier)
        if pair not in index:
            raise DrawerEmpty(f"nothing stored for {identifier!r}/{pair}")
        runs: list[dict] = []
        for d in self._identifier_dir(identifier).iterdir():
            m = RUN_RE.match(d.name)
            if m is None or m.group("pair") != pair:
                continue
            meta_path = d / RUN_METADATA_FILE
            try:
                entry = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
                raise DrawerCorrupt(f"unreadable {meta_path}: {e}") from e
            if not isinstance(entry, dict) or "trained_timestamp" not in entry:
                raise DrawerCorrupt(f"{meta_path} has the wrong shape")
            runs.append(entry)
        runs.sort(key=lambda e: (-int(e["trained_timestamp"]), e["subdirectory"]))
        return runs

    # -- retention ----------------------------------------------------------

    def purge(self, *, identifier: str, pair: str, keep: int = 1) -> list[str]:
        """Delete the OLDEST runs beyond `keep`. The newest run (the one the
        index points at) is never removable; the index and the newest run
        dir must agree before anything is touched. Returns removed names."""
        if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
            raise ValueError(f"keep must be a positive int, got {keep!r}")
        pair = _pair_check(pair)
        index = self._load_index(identifier)
        if pair not in index:
            raise DrawerEmpty(f"nothing stored for {identifier!r}/{pair}")
        newest_name = index[pair]["subdirectory"]

        matching: list[tuple[int, Path]] = []
        for d in self._identifier_dir(identifier).iterdir():
            m = RUN_RE.match(d.name)
            if m is not None and m.group("pair") == pair:
                matching.append((int(m.group("ts")), d))
        if not matching:
            raise DrawerCorrupt(
                f"index points at {identifier!r}/{pair} but no run dir exists"
            )
        matching.sort(key=lambda t: (-t[0], t[1].name))
        if matching[0][1].name != newest_name:
            raise DrawerCorrupt(
                f"newest run dir {matching[0][1].name!r} disagrees with the "
                f"index entry {newest_name!r} (torn write?)"
            )
        removed: list[str] = []
        for _ts, d in matching[keep:]:
            shutil.rmtree(d)
            removed.append(d.name)
        return removed
