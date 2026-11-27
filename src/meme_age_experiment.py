"""Matched-pair paper experiment: ``age_variant_v1``."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .meme_satellite import TokenSnapshot, scream_filter

EXPERIMENT_VERSION = "age_variant_v1"
VARIANT_MIN_AGE_S = 1_800.0


@dataclass(frozen=True)
class AgeExperimentConfig:
    version: str = EXPERIMENT_VERSION
    variant_min_age_s: float = VARIANT_MIN_AGE_S
    paper_notional_usd: float = 25.0
    max_open_positions: int = 3
    minimum_completed_trades: int = 500


def evaluate_pair(snapshot: TokenSnapshot, *, observed_at: float,
                  config: AgeExperimentConfig = AgeExperimentConfig()) -> dict:
    """Evaluate baseline and variant from one immutable causal snapshot."""
    if not isinstance(observed_at, (int, float)) or observed_at <= 0:
        raise ValueError("observed_at must be a positive timestamp")
    baseline = scream_filter(snapshot)
    passed = baseline.passed
    rejected_by = baseline.rejected_by
    reasons = list(baseline.reasons)
    age = snapshot.age_s
    if passed:
        if not isinstance(age, (int, float)) or isinstance(age, bool):
            passed, rejected_by = False, "age_missing"
            reasons.append("age unavailable for variant rule")
        elif age < config.variant_min_age_s:
            passed, rejected_by = False, "variant_min_age"
            reasons.append(f"age {age:.1f}s (< {config.variant_min_age_s:.1f}s variant minimum)")
        else:
            reasons.append("variant age rule passed")
    return {"experiment": config.version, "mint": snapshot.mint,
            "observed_at": float(observed_at), "snapshot": asdict(snapshot),
            "baseline": {"passed": baseline.passed, "rejected_by": baseline.rejected_by, "reasons": baseline.reasons},
            "variant": {"passed": passed, "rejected_by": rejected_by, "reasons": reasons},
            "paper_notional_usd": config.paper_notional_usd}


def append_decision(path: str | Path, decision: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(decision, sort_keys=True, default=str) + "\n")


def record_pair(snapshot: TokenSnapshot, *, output: str | Path,
                observed_at: float | None = None,
                config: AgeExperimentConfig = AgeExperimentConfig()) -> dict:
    decision = evaluate_pair(snapshot, observed_at=observed_at or time.time(), config=config)
    append_decision(output, decision)
    return decision
