"""Atomic order-claim regressions for the API/scheduler boundary."""
from __future__ import annotations

import time

import pytest

from src.execution.ownership import OrderClaimConflict, OrderClaimStore


def test_only_one_owner_can_claim_a_client_order(tmp_path):
    first = OrderClaimStore(str(tmp_path))
    second = OrderClaimStore(str(tmp_path))
    first.claim("signal-123", "api")
    with pytest.raises(OrderClaimConflict):
        second.claim("signal-123", "scheduler")


def test_expired_lease_can_be_reclaimed(tmp_path):
    store = OrderClaimStore(str(tmp_path), lease_seconds=0.01)
    store.claim("signal-123", "api")
    time.sleep(0.03)
    store.claim("signal-123", "scheduler")
