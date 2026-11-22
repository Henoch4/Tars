"""Durable idempotency and intent state for order submission.

This is deliberately a small SQLite boundary: the single VPS needs CP
semantics for order identity and state, not a queue or a distributed service.
Unknown outcomes remain unknown until a venue lookup reconciles them.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


INTENT_STATES = frozenset({
    "CREATED", "AUTHORIZED", "SUBMITTING", "UNKNOWN", "ACKED", "FAILED",
})
_TRANSITIONS = {
    "CREATED": {"AUTHORIZED", "FAILED"},
    "AUTHORIZED": {"SUBMITTING", "FAILED"},
    "SUBMITTING": {"UNKNOWN", "ACKED", "FAILED"},
    "UNKNOWN": {"SUBMITTING", "ACKED", "FAILED"},
    "ACKED": set(),
    "FAILED": set(),
}


class IntentConflict(RuntimeError):
    """A logical order already has an active or terminal intent."""


class InvalidIntentTransition(RuntimeError):
    """A caller attempted to skip or reverse a state transition."""


@dataclass(frozen=True)
class IntentRecord:
    intent_id: str
    client_oid: str
    logical_key: str
    owner: str
    state: str
    created_at: float
    updated_at: float
    venue_order_id: str | None = None
    error: str | None = None


class IntentStore:
    def __init__(self, path: str, *, busy_timeout_ms: int = 5000):
        self.path = Path(path)
        # Runtime construction may happen before any order is submitted. The
        # durable parent must therefore exist at initialization, including on
        # a fresh host and during API import in an isolated test directory.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), timeout=busy_timeout_ms / 1000,
            check_same_thread=False,
        )
        # WAL is preferred for the VPS, but some Windows/locked-directory
        # environments cannot switch journal mode during startup. Falling back
        # to the default rollback journal preserves the CP boundary and keeps
        # runtime construction from failing before any order exists.
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS order_intents (
                intent_id TEXT PRIMARY KEY,
                client_oid TEXT NOT NULL UNIQUE,
                logical_key TEXT NOT NULL UNIQUE,
                owner TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                venue_order_id TEXT,
                error TEXT
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_intents_state "
            "ON order_intents(state)"
        )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _record(row) -> IntentRecord:
        return IntentRecord(*row)

    def get(self, logical_key: str) -> IntentRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT intent_id,client_oid,logical_key,owner,state,created_at,"
                "updated_at,venue_order_id,error FROM order_intents "
                "WHERE logical_key=?", (logical_key,)
            ).fetchone()
        return self._record(row) if row else None

    def create(self, *, intent_id: str, client_oid: str, logical_key: str,
               owner: str) -> IntentRecord:
        if not client_oid or not logical_key or not owner:
            raise ValueError("intent identity requires client_oid, logical_key, and owner")
        now = time.time()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO order_intents VALUES (?,?,?,?,?,?,?,?,?)",
                    (intent_id, client_oid, logical_key, owner, "CREATED",
                     now, now, None, None),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as e:
                self._conn.rollback()
                existing = self._conn.execute(
                    "SELECT state FROM order_intents WHERE logical_key=? OR client_oid=?",
                    (logical_key, client_oid),
                ).fetchone()
                state = existing[0] if existing else "unknown"
                raise IntentConflict(
                    f"logical order {logical_key!r} already has an intent ({state}); "
                    "reconcile it before retrying"
                ) from e
        return self.get(logical_key)  # type: ignore[return-value]

    def transition(self, logical_key: str, new_state: str, *,
                   venue_order_id: str | None = None,
                   error: str | None = None) -> IntentRecord:
        if new_state not in INTENT_STATES:
            raise InvalidIntentTransition(f"unknown intent state {new_state!r}")
        with self._lock:
            row = self._conn.execute(
                "SELECT intent_id,client_oid,logical_key,owner,state,created_at,"
                "updated_at,venue_order_id,error FROM order_intents "
                "WHERE logical_key=?", (logical_key,)
            ).fetchone()
            if not row:
                raise KeyError(logical_key)
            current = row[4]
            if new_state not in _TRANSITIONS[current]:
                raise InvalidIntentTransition(
                    f"cannot transition {current} -> {new_state}"
                )
            self._conn.execute(
                "UPDATE order_intents SET state=?,updated_at=?,venue_order_id=COALESCE(?,venue_order_id),error=? WHERE logical_key=?",
                (new_state, time.time(), venue_order_id, error, logical_key),
            )
            self._conn.commit()
        return self.get(logical_key)  # type: ignore[return-value]

    def reconcile_unknown(self, logical_key: str, *, venue_order_id: str | None = None,
                          failed: bool = False, error: str | None = None) -> IntentRecord:
        """Resolve an ambiguous submission after an independent venue lookup.

        ``UNKNOWN`` never transitions back to ``AUTHORIZED``. A confirmed
        venue order becomes ``ACKED``; an explicit operator/venue negative
        becomes ``FAILED``. This makes retry permission an evidence-based
        decision instead of an implicit timeout retry.
        """
        record = self.get(logical_key)
        if record is None:
            raise KeyError(logical_key)
        if record.state != "UNKNOWN":
            raise InvalidIntentTransition(
                f"only UNKNOWN intents can be reconciled (got {record.state})"
            )
        if failed == bool(venue_order_id):
            raise ValueError("reconciliation requires exactly one of venue_order_id or failed")
        return self.transition(
            logical_key, "FAILED" if failed else "ACKED",
            venue_order_id=venue_order_id, error=error,
        )
