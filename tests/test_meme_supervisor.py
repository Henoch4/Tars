from pathlib import Path

from src.meme_supervisor import SatelliteSupervisor, SupervisorConfig, SupervisorState


def test_disabled_supervisor_never_calls_handlers(tmp_path: Path):
    calls = []
    supervisor = SatelliteSupervisor(
        SupervisorConfig(enabled=False, state_path=str(tmp_path / "state.json"), event_log_path=str(tmp_path / "events.jsonl")),
        screen_fn=lambda: calls.append("screen") or [{"mint": "x"}],
        reconcile_fn=lambda: calls.append("reconcile") or {"ok": True},
    )
    row = supervisor.run_once()
    assert row["event"] == "disabled_cycle"
    assert calls == []
    assert supervisor.state == SupervisorState.DISABLED


def test_shadow_cycle_reconciles_and_never_calls_entry(tmp_path: Path):
    calls = []
    supervisor = SatelliteSupervisor(
        SupervisorConfig(enabled=True, live_enabled=False, state_path=str(tmp_path / "state.json"), event_log_path=str(tmp_path / "events.jsonl")),
        screen_fn=lambda: [{"mint": "x"}],
        entry_fn=lambda _: calls.append("entry") or {"broadcast": True},
        exit_fn=lambda: calls.append("exit") or [],
        reconcile_fn=lambda: calls.append("reconcile") or {"ok": True},
    )
    row = supervisor.run_once()
    assert row["mode"] == "shadow"
    assert calls == ["reconcile", "exit"]
    assert row["observations"][0]["result"]["broadcast"] is False


def test_reconciliation_failure_halts_and_prevents_screen(tmp_path: Path):
    calls = []
    supervisor = SatelliteSupervisor(
        SupervisorConfig(enabled=True, state_path=str(tmp_path / "state.json"), event_log_path=str(tmp_path / "events.jsonl")),
        screen_fn=lambda: calls.append("screen") or [],
        reconcile_fn=lambda: {"ok": False, "reason": "mismatch"},
    )
    row = supervisor.run_once()
    assert row["event"] == "halted"
    assert supervisor.state == SupervisorState.HALTED
    assert calls == []


def test_live_cycle_calls_entry_only_when_both_flags_enabled(tmp_path: Path):
    calls = []
    supervisor = SatelliteSupervisor(
        SupervisorConfig(enabled=True, live_enabled=True, state_path=str(tmp_path / "state.json"), event_log_path=str(tmp_path / "events.jsonl")),
        screen_fn=lambda: [{"mint": "x"}],
        entry_fn=lambda _: calls.append("entry") or {"broadcast": False, "reason": "test"},
        reconcile_fn=lambda: {"ok": True},
    )
    row = supervisor.run_once()
    assert row["mode"] == "live"
    assert calls == ["entry"]


def test_emergency_stop_attempts_flatten_then_halts(tmp_path: Path):
    calls = []
    supervisor = SatelliteSupervisor(
        SupervisorConfig(enabled=True, state_path=str(tmp_path / "state.json"), event_log_path=str(tmp_path / "events.jsonl")),
        flatten_fn=lambda: calls.append("flatten") or {"unresolved": []},
    )
    row = supervisor.emergency_stop("kill switch")
    assert calls == ["flatten"]
    assert row["event"] == "emergency_stop"
    assert supervisor.state == SupervisorState.HALTED


def test_supervisor_config_is_inert_by_default():
    config = (Path(__file__).resolve().parents[1] / "config" / "meme_satellite_supervisor.yaml").read_text(encoding="utf-8")
    assert "enabled: false" in config
    assert "live_enabled: false" in config
