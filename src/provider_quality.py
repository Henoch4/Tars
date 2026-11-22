"""Uniform provider reliability and evidence-quality measurement."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Literal
import json
from pathlib import Path

ProviderStatus = Literal["complete", "unknown", "error", "rate_limited", "stale", "conflict"]


@dataclass(frozen=True)
class ProviderObservation:
    provider: str
    status: ProviderStatus
    observed_at: float
    age_seconds: float = 0.0
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider name required")
        if self.observed_at < 0 or self.age_seconds < 0:
            raise ValueError("provider timestamps cannot be negative")
        if self.status in ("error", "rate_limited", "stale", "conflict", "unknown") and not self.reason:
            raise ValueError("non-complete provider observations require a reason")


@dataclass(frozen=True)
class ProviderQualityReport:
    observations: int
    complete: int
    status_counts: dict[str, int]
    provider_counts: dict[str, int]
    conflicts: int
    rate_limited: int

    @property
    def completeness_pct(self) -> float:
        return round(self.complete * 100.0 / self.observations, 3) if self.observations else 0.0


def measure_provider_quality(rows: Iterable[ProviderObservation]) -> ProviderQualityReport:
    rows = list(rows)
    statuses = Counter(row.status for row in rows)
    providers = Counter(row.provider for row in rows)
    return ProviderQualityReport(
        observations=len(rows), complete=statuses["complete"],
        status_counts=dict(statuses.items()), provider_counts=dict(providers.items()),
        conflicts=statuses["conflict"], rate_limited=statuses["rate_limited"],
    )


class ProviderQualityRegistry:
    """Append-only JSONL registry for provider observations."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, observation: ProviderObservation) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "provider": observation.provider,
                "status": observation.status,
                "observed_at": observation.observed_at,
                "age_seconds": observation.age_seconds,
                "reason": observation.reason,
            }, sort_keys=True) + "\n")

    def report(self) -> ProviderQualityReport:
        rows: list[ProviderObservation] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                    rows.append(ProviderObservation(**value))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
        return measure_provider_quality(rows)
