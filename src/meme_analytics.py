"""Pure, read-only analytics for short memecoin satellite windows."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class WindowMetrics:
    rows: int
    screens: int
    entries: int
    exits: int
    blocked: int
    joined: int
    net_usd: float
    rejection_mix: dict[str, int]
    outcome_mix: dict[str, int]


def classify_outcome(reason: str | None) -> str:
    value = str(reason or "").upper()
    if value.startswith("RUG"):
        return "rug"
    if value.startswith("PIVOT"):
        return "pivot"
    if value.startswith("TIME"):
        return "time_box"
    return "other"


def join_trades(rows: Iterable[dict]) -> list[dict]:
    last: dict[str, dict] = {}
    joined: list[dict] = []
    for row in sorted(rows, key=lambda item: item.get("ts") or 0):
        mint = row.get("mint")
        if not isinstance(mint, str):
            continue
        if row.get("event") == "entry":
            last[mint] = row
        elif row.get("event") == "exit" and mint in last:
            entry = last[mint]
            joined.append({"mint": mint, "reason": row.get("reason", ""),
                           "net_usd": row.get("net_usd"),
                           "hold_time_s": row.get("hold_time_s"),
                           "entry_ts": entry.get("ts"), "exit_ts": row.get("ts")})
    return joined


def measure_window(rows: Iterable[dict]) -> WindowMetrics:
    materialized = list(rows)
    joined = join_trades(materialized)
    nets = [r["net_usd"] for r in joined if isinstance(r.get("net_usd"), (int, float))]
    screens = [r for r in materialized if r.get("event") == "screen"]
    outcomes = Counter(classify_outcome(r.get("reason")) for r in joined)
    rejects = Counter(r.get("rejected_by") or "unknown" for r in screens)
    return WindowMetrics(
        rows=len(materialized), screens=len(screens),
        entries=sum(r.get("event") == "entry" for r in materialized),
        exits=sum(r.get("event") == "exit" for r in materialized),
        blocked=sum(r.get("event") == "entry_blocked" for r in materialized),
        joined=len(joined), net_usd=float(sum(nets)),
        rejection_mix=dict(rejects), outcome_mix=dict(outcomes),
    )
