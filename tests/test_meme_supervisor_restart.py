from src.meme_supervisor import SatelliteSupervisor, SupervisorConfig, SupervisorState


def test_restart_restores_safe_counters_and_halt_reason(tmp_path):
    config = SupervisorConfig(
        enabled=True, state_path=str(tmp_path / "state.json"),
        event_log_path=str(tmp_path / "events.jsonl"),
    )
    first = SatelliteSupervisor(config, reconcile_fn=lambda: {"ok": False})
    first.run_once()
    second = SatelliteSupervisor(config)
    assert second.restore_state() is True
    assert second.state is SupervisorState.HALTED
    assert second.cycles == 1
    assert second.last_error == "reconciliation mismatch or unreadable"


def test_restart_rejects_corrupt_state_and_halts(tmp_path):
    state = tmp_path / "state.json"
    state.write_text('{"state":"running","cycles":"bad"}', encoding="utf-8")
    supervisor = SatelliteSupervisor(SupervisorConfig(
        enabled=True, state_path=str(state),
        event_log_path=str(tmp_path / "events.jsonl"),
    ))
    assert supervisor.restore_state() is False
    assert supervisor.state is SupervisorState.HALTED
    assert supervisor.last_error == "invalid durable supervisor state"
