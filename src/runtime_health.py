"""Sanitized cross-component health reconciliation."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class HealthSnapshot:
    component: str
    healthy: bool
    mode: str
    state: str
    blocker: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def reconcile_health(*, runtime_status: dict[str, Any],
                     lifecycle_status: Any | None = None,
                     worker_heartbeat: float | None = None,
                     now: float | None = None,
                     heartbeat_timeout_s: float = 300.0) -> list[HealthSnapshot]:
    """Return comparable API/worker/lifecycle health without secrets."""
    snapshots: list[HealthSnapshot] = []
    dry_run = bool(runtime_status.get("dry_run", True))
    snapshots.append(HealthSnapshot(
        "runtime", bool(runtime_status.get("runtime_initialized")),
        "paper" if dry_run else "live", "ready" if runtime_status.get("runtime_initialized") else "blocked",
        None if runtime_status.get("runtime_initialized") else "runtime not initialized",
        {k: runtime_status.get(k) for k in ("role", "audit_configured", "configuration_digest") if k in runtime_status},
    ))
    if worker_heartbeat is not None and now is not None:
        age = now - worker_heartbeat
        worker_ok = age <= heartbeat_timeout_s
        snapshots.append(HealthSnapshot("worker", worker_ok, "paper" if dry_run else "live",
                                        "running" if worker_ok else "stale",
                                        None if worker_ok else "worker heartbeat stale",
                                        {"heartbeat_age_s": age}))
    if lifecycle_status is not None:
        state = getattr(lifecycle_status, "state", "unknown")
        state = getattr(state, "value", str(state))
        blocker = getattr(lifecycle_status, "blocked_reason", None)
        snapshots.append(HealthSnapshot("lifecycle", state not in ("failed", "stopped"),
                                        "paper" if getattr(lifecycle_status, "dry_run", True) else "live",
                                        state, blocker))
    return snapshots
