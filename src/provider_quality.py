"""Provider reliability metrics for paper/satellite evidence pipelines."""
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
    observed_at: float | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ProviderQualityReport:
    observations: int
    complete: int
    completeness_pct: float
    status_counts: dict[str, int]
    provider_counts: dict[str, int]
    failure_reasons: dict[str, int]

    @property
    def conflicts(self) -> int:
        """Count explicit provider conflicts for report consumers."""
        return int(self.status_counts.get("conflict", 0))

    @property
    def rate_limited(self) -> int:
        """Count rate-limit outcomes for report consumers."""
        return int(self.status_counts.get("rate_limited", 0))


def measure_provider_quality(observations: Iterable[ProviderObservation]) -> ProviderQualityReport:
    rows = list(observations)
    statuses = Counter(row.status for row in rows)
    providers = Counter(row.provider for row in rows)
    reasons = Counter(row.reason or "unspecified" for row in rows if row.status != "complete")
    complete = statuses["complete"]
    total = len(rows)
    return ProviderQualityReport(
        observations=total,
        complete=complete,
        completeness_pct=round(complete * 100 / total, 3) if total else 0.0,
        status_counts=dict(statuses),
        provider_counts=dict(providers),
        failure_reasons=dict(reasons),
    )


def classify_satellite_rows(rows: Iterable[dict]) -> ProviderQualityReport:
    """Measure explicit provider metadata without inventing success.

    Older blotter rows lack provider metadata and are therefore counted as
    ``unknown`` rather than silently treated as complete.
    """
    observations: list[ProviderObservation] = []
    for row in rows:
        statuses = row.get("provider_status") or row.get("provider_statuses")
        if isinstance(statuses, dict):
            for provider, status in statuses.items():
                normalized = str(status).lower()
                allowed = {"complete", "unknown", "error", "rate_limited", "stale", "conflict"}
                observations.append(ProviderObservation(
                    str(provider), normalized if normalized in allowed else "unknown",
                    row.get("ts"), row.get("provider_reason"),
                ))
        elif row.get("event") == "screen":
            observations.append(ProviderObservation("provider_metadata", "unknown", row.get("ts"), "provider status absent"))
    return measure_provider_quality(observations)


def provider_quality_from_jsonl(path: str | Path) -> ProviderQualityReport:
    """Read a blotter defensively; malformed rows are ignored."""
    source = Path(path)
    if not source.exists():
        return classify_satellite_rows([])
    rows: list[dict] = []
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return classify_satellite_rows(rows)
