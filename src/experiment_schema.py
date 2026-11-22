"""Unified, evidence-first schema for strategy and satellite experiments."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Literal
import json

ExperimentState = Literal["planned", "running", "paused", "complete", "blocked", "invalid"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    version: str
    name: str
    hypothesis: str
    state: ExperimentState
    mode: Literal["paper", "shadow", "live"]
    sample_target: int
    sample_count: int = 0
    evidence: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    started_at: str | None = None
    completed_at: str | None = None

    def __post_init__(self) -> None:
        if not self.experiment_id.strip() or not self.version.strip() or not self.name.strip():
            raise ValueError("experiment identity is required")
        if not self.hypothesis.strip():
            raise ValueError("hypothesis is required")
        if self.sample_target < 0 or self.sample_count < 0:
            raise ValueError("sample counts cannot be negative")
        if self.sample_count > self.sample_target and self.sample_target > 0:
            raise ValueError("sample_count cannot exceed sample_target")
        if self.mode == "live":
            raise ValueError("live experiments require a separate authorization path")
        if self.state == "complete" and not self.evidence:
            raise ValueError("completed experiment requires evidence")

    @classmethod
    def planned(cls, *, experiment_id: str, version: str, name: str,
                hypothesis: str, mode: Literal["paper", "shadow"] = "paper",
                sample_target: int = 0) -> "ExperimentRecord":
        return cls(experiment_id, version, name, hypothesis, "planned", mode,
                   sample_target, started_at=_now())

    def with_update(self, **changes: Any) -> "ExperimentRecord":
        values = asdict(self)
        values.update(changes)
        values["evidence"] = tuple(values.get("evidence", ()))
        values["blockers"] = tuple(values.get("blockers", ()))
        return ExperimentRecord(**values)

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
