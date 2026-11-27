from src.meme_supervisor import SatelliteSupervisor, SupervisorConfig, SupervisorState
from src.strategy_lifecycle import LifecycleState


def test_supervisor_exposes_canonical_disabled_status(tmp_path):
    supervisor = SatelliteSupervisor(SupervisorConfig(
        enabled=False,
        state_path=str(tmp_path / "state.json"),
        event_log_path=str(tmp_path / "events.jsonl"),
    ))
    status = supervisor.lifecycle_status()
    assert status.state is LifecycleState.STOPPED
    assert status.dry_run is True
    assert status.live_enabled is False


def test_supervisor_halt_exposes_failure_reason(tmp_path):
    supervisor = SatelliteSupervisor(SupervisorConfig(
        enabled=True,
        reconcile_every_cycles=1,
        state_path=str(tmp_path / "state.json"),
        event_log_path=str(tmp_path / "events.jsonl"),
    ))
    supervisor.run_once()
    status = supervisor.lifecycle_status()
    assert supervisor.state is SupervisorState.HALTED
    assert status.state is LifecycleState.FAILED
    assert status.blocked_reason
