from dataclasses import dataclass

from src.meme_satellite_runtime import build_single_position_supervisor
from src.meme_supervisor import SupervisorConfig, SupervisorState


@dataclass
class Position:
    mint: str = "mint"
    token_amount: float = 1.0
    decimals: int = 6
    sell_executed: bool = False


def test_wired_runtime_reconciles_before_screen_and_stays_shadow(tmp_path):
    order = []
    pos = Position()
    supervisor = build_single_position_supervisor(
        SupervisorConfig(enabled=True, live_enabled=False,
                         state_path=str(tmp_path / "state.json"),
                         event_log_path=str(tmp_path / "events.jsonl")),
        screen_fn=lambda: order.append("screen") or [{"mint": "candidate"}],
        position_loader=lambda: order.append("load") or pos,
        position_saver=lambda _p: order.append("save"),
        balance_reader=lambda _m, _o: order.append("balance") or 1_000_000,
        owner="owner", sell_fn=lambda **_: order.append("sell"),
        entry_fn=lambda _c: order.append("entry") or {"broadcast": True},
    )
    row = supervisor.run_once()
    assert row["mode"] == "shadow"
    assert "entry" not in order and "sell" not in order
    assert order[:3] == ["load", "balance", "screen"]
    assert supervisor.state == SupervisorState.RUNNING


def test_wired_runtime_halts_before_screen_on_mismatch(tmp_path):
    order = []
    supervisor = build_single_position_supervisor(
        SupervisorConfig(enabled=True, state_path=str(tmp_path / "state.json"),
                         event_log_path=str(tmp_path / "events.jsonl")),
        screen_fn=lambda: order.append("screen") or [],
        position_loader=lambda: Position(),
        position_saver=lambda _p: None,
        balance_reader=lambda _m, _o: 999,
        owner="owner", sell_fn=lambda **_: None,
    )
    row = supervisor.run_once()
    assert row["event"] == "halted"
    assert order == []


def test_wired_emergency_stop_uses_flatten_adapter(tmp_path):
    calls = []
    supervisor = build_single_position_supervisor(
        SupervisorConfig(enabled=True, state_path=str(tmp_path / "state.json"),
                         event_log_path=str(tmp_path / "events.jsonl")),
        screen_fn=lambda: [], position_loader=lambda: None,
        position_saver=lambda _p: None, balance_reader=lambda _m, _o: 0,
        owner="owner", sell_fn=lambda **_: calls.append("sell"),
    )
    report = supervisor.emergency_stop("operator kill")
    assert report["event"] == "emergency_stop"
    assert supervisor.state == SupervisorState.HALTED
    assert calls == []
