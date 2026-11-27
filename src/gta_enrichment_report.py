"""Deterministic completeness report for GTA shadow observations."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .gta_enrichment import REQUIRED_FIELDS
from .provider_quality import ProviderObservation, measure_provider_quality


def iter_observations(path: str | Path, *, since_ts: float | None = None) -> Iterable[dict[str, Any]]:
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if (isinstance(row, dict) and isinstance(row.get("mint"), str)
                and (since_ts is None or float(row.get("ts", 0)) >= since_ts)):
            yield row


def build_report(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    missing: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    source_errors: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    provider_rows: list[ProviderObservation] = []
    allowed = 0
    legacy_rows = 0
    for row in rows:
        allowed += int(bool(row.get("allowed")))
        row_missing = row.get("enrichment_missing")
        if not isinstance(row_missing, list):
            # Rows written before the completeness contract was deployed are
            # historical, not complete. Never let absent metadata inflate the
            # new milestone's success rate.
            legacy_rows += 1
            row_missing = list(REQUIRED_FIELDS)
        for field in row_missing:
            missing[str(field)] += 1
        for source, status in (row.get("enrichment_sources") or {}).items():
            sources[f"{source}:{status}"] += 1
            normalized = str(status).lower()
            mapped = ("complete" if normalized in ("ok", "complete", "success")
                      else "unknown" if normalized in ("unknown", "missing")
                      else "rate_limited" if "429" in normalized or "rate" in normalized
                      else "error")
            provider_rows.append(ProviderObservation(
                str(source), mapped, float(row.get("ts", 0) or 0),
                reason=None if mapped == "complete" else str(status),
            ))
        for source, error in (row.get("enrichment_errors") or {}).items():
            source_errors[f"{source}:{error}"] += 1
            provider_rows.append(ProviderObservation(
                str(source), "error", float(row.get("ts", 0) or 0),
                reason=str(error),
            ))
        reasons[str(row.get("reason", "unknown"))] += 1
    total = len(rows)
    complete = sum(
        1 for row in rows
        if isinstance(row.get("enrichment_missing"), list)
        and not row.get("enrichment_missing")
    )
    provider_quality = measure_provider_quality(provider_rows)
    return {
        "observations": total,
        "allowed": allowed,
        "complete_safety_payloads": complete,
        "completeness_pct": round(100.0 * complete / total, 3) if total else 0.0,
        "legacy_rows_without_completeness_metadata": legacy_rows,
        "field_missing": {field: missing[field] for field in REQUIRED_FIELDS},
        "source_status": dict(sources),
        "source_errors": dict(source_errors),
        "provider_quality": {
            "observations": provider_quality.observations,
            "complete": provider_quality.complete,
            "completeness_pct": provider_quality.completeness_pct,
            "status_counts": provider_quality.status_counts,
            "provider_counts": provider_quality.provider_counts,
            "conflicts": provider_quality.conflicts,
            "rate_limited": provider_quality.rate_limited,
        },
        "rejection_reasons": dict(reasons),
    }


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Report GTA shadow enrichment completeness")
    parser.add_argument("input", help="GTA shadow JSONL path")
    parser.add_argument("--since-ts", type=float, default=None,
                        help="Only include observations at or after this Unix timestamp")
    args = parser.parse_args(argv)
    print(json.dumps(build_report(iter_observations(args.input, since_ts=args.since_ts)),
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
