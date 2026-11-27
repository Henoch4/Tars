"""Create a sanitized, reviewable short-window analytics artifact."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make direct execution from `scripts/` resolve the repository package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.meme_analytics import measure_window
from src.provider_quality import classify_satellite_rows


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def build_report(path: Path) -> dict:
    rows = load_rows(path)
    metrics = measure_window(rows)
    provider = classify_satellite_rows(rows)
    return {
        "artifact_type": "meme_satellite_window_metrics",
        "source": path.name,
        "read_only": True,
        "metrics": {
            "rows": metrics.rows, "screens": metrics.screens,
            "entries": metrics.entries, "exits": metrics.exits,
            "blocked": metrics.blocked, "joined": metrics.joined,
            "net_usd": metrics.net_usd,
            "rejection_mix": metrics.rejection_mix,
            "outcome_mix": metrics.outcome_mix,
            "provider_quality": {
                "observations": provider.observations,
                "complete": provider.complete,
                "completeness_pct": provider.completeness_pct,
                "status_counts": provider.status_counts,
                "provider_counts": provider.provider_counts,
                "failure_reasons": provider.failure_reasons,
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if not args:
        print("usage: meme_window_report.py BLOTTER.jsonl [OUTPUT.json]")
        return 2
    source = Path(args[0])
    report = build_report(source)
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if len(args) > 1:
        Path(args[1]).write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
