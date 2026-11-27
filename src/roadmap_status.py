"""Repository-wide roadmap status projection from existing evidence."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable


@dataclass(frozen=True)
class RoadmapPhase:
    phase: str
    title: str
    status: str
    evidence: tuple[str, ...] = ()
    blocker: str | None = None


def build_status(*, test_count: int, safety_freeze: bool,
                 deployment_ready: bool) -> tuple[RoadmapPhase, ...]:
    return (
        RoadmapPhase("A", "Runtime and live interlocks", "complete",
                     ("runtime lifecycle", "safety freeze")),
        RoadmapPhase("B", "Intent ownership and reconciliation", "complete",
                     ("intent store", "execution ownership")),
        RoadmapPhase("C", "Defensive input semantics", "complete",
                     ("data integrity", "risk gate")),
        RoadmapPhase("D", "Paper/agent parity and SafetyNet", "active",
                     ("parity evidence", "SafetyNet tests")),
        RoadmapPhase("E", "FPZ lifecycle accounting", "active",
                     ("quantity-first ledger", "settled funding")),
        RoadmapPhase("F", "QA and release artifact", "complete" if safety_freeze and deployment_ready else "active",
                     (f"{test_count} tests", "fresh-host smoke"),
                     None if safety_freeze and deployment_ready else "release gates incomplete"),
        RoadmapPhase("GTA", "Historical analytics only", "gated",
                     ("case study", "read-only reports"), "live promotion forbidden"),
    )


def status_dict(**kwargs) -> list[dict]:
    return [asdict(phase) for phase in build_status(**kwargs)]
