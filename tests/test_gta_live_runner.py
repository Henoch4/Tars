from datetime import datetime, timezone

import pytest

from src.gta_book import GtaBook
from src.gta_event import GtaEventPolicy
from src.gta_live_runner import GtaLiveRunner, RunnerState
from src.gta_reconciliation import ReconciliationResult


def _policy(enabled=True, live_enabled=True):
    return GtaEventPolicy(
        enabled=enabled,
        live_enabled=live_enabled,
        start_utc=datetime(2026, 11, 18, tzinfo=timezone.utc),
        end_utc=datetime(2026, 11, 22, tzinfo=timezone.utc),
        max_total_usd=9,
        max_per_token_usd=3,
        max_open_positions=3,
    )


def _candidate():
    return {
        "mint": "m1", "name": "GTA VI", "symbol": "GTA6",
        "size_usd": 3.0, "entry_price": 1.0, "price_usd": 1.0,
        "sol_price_usd": 100.0, "liquidity_usd": 100.0,
        "top10_hold_pct": 10.0, "holder_count": 200,
        "mint_authority_disabled": True, "freeze_authority_disabled": True,
        "honeypot": False, "transfer_tax_pct": 0.0,
    }


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def enter(self, candidate, **kwargs):
        self.calls.append((candidate, kwargs))
        return {"broadcast": False, "reason": "test"}


@pytest.mark.asyncio
async def test_runner_stays_disabled_outside_window_and_auto_arms_inside():
    fake = FakeExecutor()
    runner = GtaLiveRunner(
        _policy(), GtaBook(_policy()), discovery=lambda: [_candidate()],
        executor=fake,
    )
    outside = await runner.run_once(datetime(2026, 11, 17, 23, 59, tzinfo=timezone.utc))
    assert outside.state == RunnerState.DISABLED
    inside = await runner.run_once(datetime(2026, 11, 19, tzinfo=timezone.utc))
    assert inside.state == RunnerState.ARMED
    assert fake.calls


@pytest.mark.asyncio
async def test_runner_emergency_shutdown_is_terminal_for_loop():
    policy = _policy()
    runner = GtaLiveRunner(policy, GtaBook(policy), discovery=lambda: [])
    status = await runner.emergency_shutdown("test")
    assert status.state == RunnerState.EMERGENCY_SHUTDOWN
    after = await runner.run_once(datetime(2026, 11, 19, tzinfo=timezone.utc))
    assert after.state == RunnerState.EMERGENCY_SHUTDOWN


@pytest.mark.asyncio
async def test_runner_refuses_to_arm_when_reconciliation_fails():
    policy = _policy()
    runner = GtaLiveRunner(
        policy, GtaBook(policy), discovery=lambda: [],
        reconcile=lambda: ReconciliationResult(False, True, "mismatch", {}),
    )
    status = await runner.startup_reconciliation()
    assert status.state == RunnerState.DISABLED
    assert "mismatch" in status.reason
