"""Venue lookup must resolve ambiguity only with a concrete order ID."""
from __future__ import annotations

import pytest

from src.execution.intent_reconciler import IntentReconciler
from src.execution.intent_store import IntentStore


class FakeCli:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def _unknown(store: IntentStore):
    store.create(intent_id="i1", client_oid="c1", logical_key="c1", owner="api")
    store.transition("c1", "AUTHORIZED")
    store.transition("c1", "SUBMITTING")
    store.transition("c1", "UNKNOWN", error="timeout")


@pytest.mark.asyncio
async def test_lookup_reconciles_only_when_venue_returns_order_id(tmp_path):
    store = IntentStore(str(tmp_path / "intents.sqlite3"))
    _unknown(store)
    cli = FakeCli({"data": [{"ordId": "venue-1", "state": "filled"}]})
    result = await IntentReconciler(cli, store).reconcile(
        logical_key="c1", inst_id="SOL-USDT-SWAP", client_oid="c1"
    )
    assert result is not None and result.state == "ACKED"
    assert "--clOrdId" in cli.calls[0][0]


@pytest.mark.asyncio
async def test_empty_or_failed_lookup_preserves_unknown(tmp_path):
    store = IntentStore(str(tmp_path / "intents.sqlite3"))
    _unknown(store)
    cli = FakeCli({"data": []})
    assert await IntentReconciler(cli, store).reconcile(
        logical_key="c1", inst_id="SOL-USDT-SWAP", client_oid="c1"
    ) is None
    assert store.get("c1").state == "UNKNOWN"
