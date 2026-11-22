"""Read-only planner reference for the future isolated Rust/TS executor."""
from __future__ import annotations

from dataclasses import dataclass
from .intent import ExecutionIntent


@dataclass(frozen=True)
class ExecutionPlan:
    intent_id: str
    status: str
    signed: bool = False
    broadcast: bool = False
    reason: str | None = None


def plan_intent(intent: ExecutionIntent) -> ExecutionPlan:
    """Validate an already-typed intent without touching wallets or networks."""
    if intent.allow_live:
        return ExecutionPlan(intent.intent_id, "refused", reason="live planner unavailable")
    return ExecutionPlan(intent.intent_id, "planned")
