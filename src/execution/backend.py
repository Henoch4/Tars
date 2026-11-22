"""Execution backend boundary.

The first backend is intentionally read-only. A signer/broadcaster must
implement this interface in an isolated process after planner parity review.
"""
from __future__ import annotations

from typing import Protocol

from .intent import ExecutionIntent, ExecutionReceipt
from .planner import ExecutionPlan, plan_intent


class ExecutionBackend(Protocol):
    def execute(self, intent: ExecutionIntent) -> ExecutionReceipt: ...


class ReadOnlyExecutionBackend:
    """Plans intents and refuses all signing/broadcast activity."""

    def plan(self, intent: ExecutionIntent) -> ExecutionPlan:
        return plan_intent(intent)

    def execute(self, intent: ExecutionIntent) -> ExecutionReceipt:
        plan = self.plan(intent)
        if plan.status == "refused":
            return ExecutionReceipt.refused(intent.intent_id, plan.reason or "planner refusal")
        return ExecutionReceipt.refused(
            intent.intent_id,
            "read-only backend: signing and broadcast are unavailable",
            provider="read-only",
        )
