"""Durable order-intent state-machine regressions."""
from __future__ import annotations

import pytest

from src.execution.intent_store import (
    IntentConflict,
    IntentStore,
    InvalidIntentTransition,
)


def test_intent_identity_is_unique_and_survives_reopen(tmp_path):
    path = str(tmp_path / "intents.sqlite3")
    store = IntentStore(path)
    store.create(intent_id="i1", client_oid="c1", logical_key="book1:c1", owner="api")
    with pytest.raises(IntentConflict):
        store.create(intent_id="i2", client_oid="c2", logical_key="book1:c1", owner="scheduler")
    store.close()
    reopened = IntentStore(path)
    record = reopened.get("book1:c1")
    assert record is not None
    assert record.state == "CREATED"
    assert record.owner == "api"


def test_intent_transitions_reject_skips_and_preserve_unknown(tmp_path):
    store = IntentStore(str(tmp_path / "intents.sqlite3"))
    store.create(intent_id="i1", client_oid="c1", logical_key="book1:c1", owner="api")
    with pytest.raises(InvalidIntentTransition):
        store.transition("book1:c1", "ACKED")
    store.transition("book1:c1", "AUTHORIZED")
    store.transition("book1:c1", "SUBMITTING")
    record = store.transition("book1:c1", "UNKNOWN", error="timeout")
    assert record.state == "UNKNOWN"
    assert record.error == "timeout"
    store.transition("book1:c1", "SUBMITTING")
    assert store.transition("book1:c1", "ACKED", venue_order_id="venue-1").venue_order_id == "venue-1"


def test_unknown_requires_evidence_before_resolution(tmp_path):
    store = IntentStore(str(tmp_path / "intents.sqlite3"))
    store.create(intent_id="i1", client_oid="c1", logical_key="book1:c1", owner="api")
    store.transition("book1:c1", "AUTHORIZED")
    store.transition("book1:c1", "SUBMITTING")
    store.transition("book1:c1", "UNKNOWN", error="timeout")
    with pytest.raises(ValueError):
        store.reconcile_unknown("book1:c1")
    assert store.reconcile_unknown("book1:c1", venue_order_id="venue-1").state == "ACKED"


def test_unknown_can_be_explicitly_marked_failed(tmp_path):
    store = IntentStore(str(tmp_path / "intents.sqlite3"))
    store.create(intent_id="i1", client_oid="c1", logical_key="book1:c1", owner="api")
    store.transition("book1:c1", "AUTHORIZED")
    store.transition("book1:c1", "SUBMITTING")
    store.transition("book1:c1", "UNKNOWN")
    assert store.reconcile_unknown("book1:c1", failed=True, error="not found").state == "FAILED"


def test_intent_store_creates_missing_parent_directory(tmp_path):
    path = tmp_path / "nested" / "state" / "intents.sqlite3"
    store = IntentStore(str(path))
    assert path.exists()
    store.close()
