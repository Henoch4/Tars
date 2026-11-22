"""Explainable paper/agent parity comparison on identical recorded inputs."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable


@dataclass(frozen=True)
class ParityReport:
    samples: int
    matching_decisions: int
    differing_decisions: int
    differences: tuple[dict[str, Any], ...]

    @property
    def match_rate(self) -> float:
        return self.matching_decisions / self.samples if self.samples else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "match_rate": self.match_rate}


def compare_decisions(records: Iterable[dict[str, Any]]) -> ParityReport:
    differences: list[dict[str, Any]] = []
    total = matching = 0
    for index, row in enumerate(records):
        total += 1
        paper = row.get("paper")
        agent = row.get("agent")
        if paper == agent:
            matching += 1
        else:
            differences.append({"index": index, "paper": paper, "agent": agent})
    return ParityReport(total, matching, total - matching, tuple(differences))
