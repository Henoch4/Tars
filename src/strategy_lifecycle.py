"""Canonical lifecycle states shared by paper strategies and satellites."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LifecycleState(str, Enum):
    READY = "ready"
    PLANNED = "planned"
    PAPER = "paper"
    SHADOW = "shadow"
    RUNNING = "running"
    HALTED = "halted"
    PAUSED = "paused"
    GATED = "gated"
    RETIRED = "retired"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True)
class LifecycleStatus:
    name: str
    state: LifecycleState
    dry_run: bool = True
    live_enabled: bool = False
    blocked_reason: str | None = None

    def __post_init__(self) -> None:
        if self.state in (LifecycleState.FAILED, LifecycleState.GATED) and not self.blocked_reason:
            raise ValueError("blocked lifecycle requires blocked_reason")
        if self.live_enabled and self.dry_run:
            raise ValueError("live_enabled cannot be true in dry_run")


class ManagedLifecycle:
    def __init__(self, name: str, *, state: LifecycleState = LifecycleState.PAPER,
                 dry_run: bool = True, cycle=None):
        self.name = name
        self.cycle = cycle
        self._status = LifecycleStatus(name, state, dry_run, False)

    def status(self) -> LifecycleStatus:
        return self._status

    def start(self) -> LifecycleStatus:
        if self._status.state in (LifecycleState.RETIRED, LifecycleState.FAILED):
            raise RuntimeError(f"cannot start lifecycle in {self._status.state.value}")
        self._status = LifecycleStatus(self.name, LifecycleState.RUNNING, self._status.dry_run, False)
        return self._status

    def pause(self, reason: str = "paused by operator") -> LifecycleStatus:
        self._status = LifecycleStatus(self.name, LifecycleState.PAUSED, self._status.dry_run, False, reason)
        return self._status

    def run_cycle(self, snapshot):
        if self._status.state not in (LifecycleState.RUNNING, LifecycleState.READY, LifecycleState.PAPER):
            raise RuntimeError(f"lifecycle is {self._status.state.value}")
        if self.cycle is not None:
            return self.cycle(snapshot)
        return None

    def stop(self) -> LifecycleStatus:
        self._status = LifecycleStatus(self.name, LifecycleState.STOPPED, self._status.dry_run, False)
        return self._status
