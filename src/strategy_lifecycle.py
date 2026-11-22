"""Canonical lifecycle contract shared by strategies and satellites."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol
from datetime import datetime, timezone


class LifecycleState(str, Enum):
    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class LifecycleStatus:
    name: str
    state: LifecycleState
    dry_run: bool
    live_enabled: bool
    last_cycle_at: str | None = None
    blocked_reason: str | None = None

    def __post_init__(self) -> None:
        if self.live_enabled and self.dry_run:
            raise ValueError("live_enabled cannot be true in dry_run")
        if self.state == LifecycleState.FAILED and not self.blocked_reason:
            raise ValueError("failed lifecycle requires blocked_reason")


class StrategyLifecycle(Protocol):
    def status(self) -> LifecycleStatus: ...
    def start(self) -> None: ...
    def pause(self, reason: str) -> None: ...
    def stop(self, reason: str = "") -> None: ...
    def run_cycle(self, snapshot: Any) -> Any: ...


class ManagedLifecycle:
    """Small state machine existing runners can embed without rewrites."""

    def __init__(self, name: str, *, dry_run: bool = True,
                 live_enabled: bool = False,
                 cycle: Callable[[Any], Any] | None = None):
        self._name = name
        self._dry_run = dry_run
        self._live_enabled = live_enabled
        self._cycle = cycle
        self._state = LifecycleState.CREATED
        self._last_cycle_at: str | None = None
        self._blocked_reason: str | None = None
        self._assert_safe_mode()

    def _assert_safe_mode(self) -> None:
        if self._live_enabled and self._dry_run:
            raise ValueError("live_enabled cannot be true in dry_run")

    def status(self) -> LifecycleStatus:
        return LifecycleStatus(self._name, self._state, self._dry_run,
                               self._live_enabled, self._last_cycle_at,
                               self._blocked_reason)

    def start(self) -> None:
        if self._state in (LifecycleState.CREATED, LifecycleState.PAUSED):
            self._state = LifecycleState.RUNNING
            self._blocked_reason = None

    def pause(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("pause reason required")
        self._state = LifecycleState.PAUSED
        self._blocked_reason = reason

    def stop(self, reason: str = "") -> None:
        self._state = LifecycleState.STOPPED
        self._blocked_reason = reason or None

    def run_cycle(self, snapshot: Any) -> Any:
        if self._state != LifecycleState.RUNNING:
            raise RuntimeError(f"lifecycle is {self._state.value}, not running")
        if self._cycle is None:
            raise RuntimeError("cycle callback is not configured")
        result = self._cycle(snapshot)
        self._last_cycle_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return result
