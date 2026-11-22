"""Convert parity results into an append-only experiment evidence artifact."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .parity_report import ParityReport


def parity_evidence(*, experiment_id: str, report: ParityReport,
                    input_source: str) -> dict[str, Any]:
    return {
        "artifact_type": "paper_agent_parity",
        "experiment_id": experiment_id,
        "input_source": input_source,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "read_only": True,
        "samples": report.samples,
        "matching_decisions": report.matching_decisions,
        "differing_decisions": report.differing_decisions,
        "match_rate": report.match_rate,
        "differences": list(report.differences),
    }
