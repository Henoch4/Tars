import pytest

from src.strategy_lifecycle import LifecycleState, LifecycleStatus, ManagedLifecycle


def test_lifecycle_status_is_explicitly_paper_by_default():
    status = LifecycleStatus("main-satellite", LifecycleState.READY, True, False)
    assert status.state is LifecycleState.READY
    assert status.dry_run is True
    assert status.live_enabled is False


def test_live_enabled_cannot_hide_inside_dry_run():
    with pytest.raises(ValueError):
        LifecycleStatus("gta", LifecycleState.READY, True, True)


def test_failed_runner_requires_reason():
    with pytest.raises(ValueError):
        LifecycleStatus("paperz", LifecycleState.FAILED, True, False)


def test_managed_lifecycle_controls_runner_without_live_access():
    seen = []
    runner = ManagedLifecycle("paperz", cycle=lambda snapshot: seen.append(snapshot))
    runner.start()
    runner.run_cycle({"cycle": 1})
    runner.pause("provider unavailable")
    assert seen == [{"cycle": 1}]
    assert runner.status().state is LifecycleState.PAUSED
    assert runner.status().blocked_reason == "provider unavailable"
