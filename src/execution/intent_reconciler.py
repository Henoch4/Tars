"""Venue lookup adapter for ambiguous order submissions."""
from __future__ import annotations

from typing import Any

from ..okx_cli import OkxCli, OkxCliError
from .intent_store import IntentRecord, IntentStore


def _first_order(result: Any) -> dict | None:
    if isinstance(result, list):
        return result[0] if result and isinstance(result[0], dict) else None
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, list):
            return data[0] if data and isinstance(data[0], dict) else None
        return data if isinstance(data, dict) else None
    return None


class IntentReconciler:
    """Resolve only what the venue can prove; never guesses on timeout."""

    def __init__(self, cli: OkxCli, store: IntentStore):
        self.cli = cli
        self.store = store

    async def lookup(self, *, inst_id: str, client_oid: str) -> dict | None:
        try:
            result = await self.cli.run(
                "trade", "order",
                "--instId", inst_id,
                "--clOrdId", client_oid,
                use_global_flags=True,
            )
        except OkxCliError:
            # A failed lookup cannot lower certainty; UNKNOWN remains UNKNOWN.
            return None
        return _first_order(result)

    async def reconcile(self, *, logical_key: str, inst_id: str,
                        client_oid: str) -> IntentRecord | None:
        current = self.store.get(logical_key)
        if current is None or current.state != "UNKNOWN":
            return current
        order = await self.lookup(inst_id=inst_id, client_oid=client_oid)
        if not order:
            return None
        venue_order_id = str(order.get("ordId") or "").strip()
        if not venue_order_id:
            return None
        return self.store.reconcile_unknown(
            logical_key, venue_order_id=venue_order_id,
            error=f"venue_state={order.get('state', 'unknown')}",
        )
