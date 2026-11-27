"""Restart-safe supervisor for the main memecoin satellite.

The supervisor is deliberately an orchestration layer, not a strategy. It
owns lifecycle, heartbeats, durable state, reconciliation gating, and the
paper/live boundary. Discovery, execution, and chain readers are injected so
the control plane can be tested without keys or network access.

Live operation is opt-in twice: ``enabled`` starts the process and
``live_enabled`` permits entry handlers to be called. Both default to false.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from .strategy_lifecycle import LifecycleState, LifecycleStatus


class SupervisorState(str, Enum):
    DISABLED = "disabled"
    RUNNING = "running"
    HALTED = "halted"


@dataclass(frozen=True)
class SupervisorConfig:
    enabled: bool = False
    live_enabled: bool = False
    interval_s: float = 60.0
    reconcile_every_cycles: int = 1
    state_path: str = "data/meme_satellite/supervisor_state.json"
    event_log_path: str = "data/meme_satellite/supervisor_events.jsonl"


class SatelliteSupervisor:
    """Lifecycle coordinator with fail-closed reconciliation semantics."""

    def __init__(
        self,
        config: SupervisorConfig,
        *,
        screen_fn: Callable[[], list] | None = None,
        entry_fn: Callable[[Any], dict] | None = None,
        exit_fn: Callable[[], Any] | None = None,
        reconcile_fn: Callable[[], dict] | None = None,
        flatten_fn: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.screen_fn = screen_fn or (lambda: [])
        self.entry_fn = entry_fn or (lambda _candidate: {"broadcast": False, "reason": "no entry handler"})
        self.exit_fn = exit_fn or (lambda: [])
        self.reconcile_fn = reconcile_fn
        self.flatten_fn = flatten_fn
        self.clock = clock
        self.state = SupervisorState.DISABLED if not config.enabled else SupervisorState.RUNNING
        self.cycles = 0
        self.last_heartbeat = 0.0
        self.last_error = ""

    @property
    def live(self) -> bool:
        return bool(self.config.enabled and self.config.live_enabled)

    def lifecycle_status(self) -> LifecycleStatus:
        """Expose canonical control-plane status without changing behavior."""
        state = {
            SupervisorState.DISABLED: LifecycleState.STOPPED,
            SupervisorState.RUNNING: LifecycleState.RUNNING,
            SupervisorState.HALTED: LifecycleState.FAILED,
        }[self.state]
        return LifecycleStatus(
            "main-memecoin-satellite", state, dry_run=not self.live,
            live_enabled=self.live,
            blocked_reason=self.last_error or ("disabled" if state == LifecycleState.STOPPED else None),
        )

    def _write_state(self) -> None:
        path = Path(self.config.state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "state": self.state.value,
            "live_enabled": self.live,
            "cycles": self.cycles,
            "last_heartbeat": self.last_heartbeat,
            "last_error": self.last_error,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def restore_state(self) -> bool:
        """Restore only durable lifecycle counters/error state after restart."""
        path = Path(self.config.state_path)
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            state = SupervisorState(payload["state"])
            cycles = int(payload["cycles"])
            heartbeat = float(payload["last_heartbeat"])
            if cycles < 0 or heartbeat < 0:
                raise ValueError("invalid durable supervisor counters")
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            self.state = SupervisorState.HALTED
            self.last_error = "invalid durable supervisor state"
            return False
        self.state = state
        self.cycles = cycles
        self.last_heartbeat = heartbeat
        self.last_error = str(payload.get("last_error") or "")
        return True

    def _record(self, event: str, **fields: Any) -> dict:
        row = {"event": event, "ts": self.clock(), "state": self.state.value, **fields}
        path = Path(self.config.event_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        return row

    def _reconcile(self) -> bool:
        if self.reconcile_fn is None:
            self.last_error = "reconciliation handler unavailable"
            self._record("reconciliation_failed", reason=self.last_error)
            return False
        try:
            report = self.reconcile_fn()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"reconciliation exception: {type(exc).__name__}"
            self._record("reconciliation_failed", reason=self.last_error)
            return False
        if not isinstance(report, dict) or report.get("ok") is not True:
            self.last_error = "reconciliation mismatch or unreadable"
            self._record("reconciliation_failed", reason=self.last_error, report=report)
            return False
        self._record("reconciliation_ok", report=report)
        return True

    def run_once(self) -> dict:
        """Run one supervised cycle; any safety failure halts new activity."""
        self.cycles += 1
        self.last_heartbeat = self.clock()
        if not self.config.enabled:
            self.state = SupervisorState.DISABLED
            row = self._record("disabled_cycle", cycle=self.cycles)
            self._write_state()
            return row
        if self.state == SupervisorState.HALTED:
            row = self._record("halted_cycle", cycle=self.cycles, reason=self.last_error)
            self._write_state()
            return row
        if self.config.reconcile_every_cycles <= 0:
            self.state = SupervisorState.HALTED
            self.last_error = "invalid reconciliation cadence"
            row = self._record("halted", reason=self.last_error)
            self._write_state()
            return row
        if self.reconcile_fn is None or (self.cycles - 1) % self.config.reconcile_every_cycles == 0:
            if not self._reconcile():
                self.state = SupervisorState.HALTED
                row = self._record("halted", reason=self.last_error)
                self._write_state()
                return row
        try:
            candidates = self.screen_fn()
            if not isinstance(candidates, list):
                raise TypeError("screen handler must return a list")
            observations = []
            for candidate in candidates:
                if self.live:
                    result = self.entry_fn(candidate)
                else:
                    result = {"broadcast": False, "reason": "shadow mode"}
                observations.append({"candidate": candidate, "result": result})
            exits = self.exit_fn()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"cycle exception: {type(exc).__name__}"
            self.state = SupervisorState.HALTED
            row = self._record("cycle_failed", cycle=self.cycles, reason=self.last_error)
            self._write_state()
            return row
        self.state = SupervisorState.RUNNING
        row = self._record(
            "cycle_completed",
            cycle=self.cycles,
            mode="live" if self.live else "shadow",
            candidates=len(candidates),
            observations=observations,
            exits=exits,
        )
        self._write_state()
        return row

    def emergency_stop(self, reason: str = "operator stop") -> dict:
        """Best-effort flatten, then halt the supervisor."""
        result: Any = {"attempted": False, "reason": "no flatten handler"}
        if self.flatten_fn is not None:
            try:
                result = {"attempted": True, "result": self.flatten_fn()}
            except Exception as exc:  # noqa: BLE001
                result = {"attempted": True, "error": type(exc).__name__}
        self.state = SupervisorState.HALTED
        self.last_error = reason
        row = self._record("emergency_stop", reason=reason, flatten=result)
        self._write_state()
        return row

    def run_forever(self, *, max_cycles: int | None = None) -> None:
        """Run until stopped; intended for a systemd service."""
        while max_cycles is None or self.cycles < max_cycles:
            self.run_once()
            if self.state == SupervisorState.HALTED:
                return
            time.sleep(max(0.1, float(self.config.interval_s)))

