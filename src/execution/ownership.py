"""Crash-tolerant order-claim boundary shared by API and scheduler.

Claims use one file per client order and ``O_EXCL`` creation, which is atomic
across Windows and POSIX processes on the same host. A short lease makes a
crashed owner recoverable; completed claims remain until the lease expires so
an accidental retry cannot immediately submit the same client order twice.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path


class OrderClaimConflict(RuntimeError):
    """Another process currently owns, or recently completed, this order."""


class OrderClaimStore:
    def __init__(self, directory: str, lease_seconds: float = 900.0):
        self.directory = Path(directory)
        self.lease_seconds = lease_seconds

    @staticmethod
    def _safe_name(client_oid: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", client_oid)[:180]

    def _path(self, client_oid: str) -> Path:
        return self.directory / f"{self._safe_name(client_oid)}.json"

    def claim(self, client_oid: str, owner: str) -> None:
        if not client_oid:
            raise ValueError("client_oid is required for execution ownership")
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(client_oid)
        now = time.time()
        payload = {"client_oid": client_oid, "owner": owner,
                   "claimed_at": now, "expires_at": now + self.lease_seconds}
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if float(existing.get("expires_at", 0)) > now:
                    raise OrderClaimConflict(
                        f"order {client_oid} is owned by another execution lease"
                    )
                path.unlink()
            except OrderClaimConflict:
                raise
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                # An unreadable claim is safer treated as active than erased.
                raise OrderClaimConflict(
                    f"order {client_oid} has an unreadable execution claim"
                )
            return self.claim(client_oid, owner)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)

