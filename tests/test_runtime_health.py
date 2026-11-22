from src.runtime_health import reconcile_health
from src.strategy_lifecycle import LifecycleState, LifecycleStatus


def test_health_reconciliation_is_sanitized_and_detects_stale_worker():
    snapshots = reconcile_health(
        runtime_status={"runtime_initialized": True, "dry_run": True,
                        "configuration_digest": "abc", "audit_configured": False},
        lifecycle_status=LifecycleStatus("satellite", LifecycleState.RUNNING, True, False),
        worker_heartbeat=10, now=400, heartbeat_timeout_s=300,
    )
    assert [s.component for s in snapshots] == ["runtime", "worker", "lifecycle"]
    assert snapshots[1].healthy is False
    assert snapshots[1].blocker == "worker heartbeat stale"
    assert "secret" not in str(snapshots)


def test_uninitialized_runtime_is_blocked():
    snapshots = reconcile_health(runtime_status={"runtime_initialized": False, "dry_run": True})
    assert snapshots[0].healthy is False
    assert snapshots[0].blocker == "runtime not initialized"
