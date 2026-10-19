"""G1 progress report — read-only measurement of the paper gate.

G1 = >=7 days of paper operation AND >=30 net-positive paper round-trips
(net-positive after the paper cost model). This module reads the append-only
blotter + positions file and reports progress toward G1. It NEVER writes,
never imports the loop, and never touches trading state.

Clock proxy: the first `entry` record's timestamp is the start of live paper
operation (the restart that began the current run). Round-trips = completed
`exit` records.
"""
from __future__ import annotations

import json
import time
from collections import Counter

G1_MIN_DAYS = 7.0
G1_MIN_ROUND_TRIPS = 30
DAY_S = 86400.0


def load_records(blotter_path) -> list:
    """Tolerant JSONL read: skip blank/corrupt lines, never raise."""
    records = []
    try:
        with open(blotter_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    except OSError:
        return []
    return records


def _events(records: list, name: str) -> list:
    return [r for r in records if r.get("event") == name]


def build_g1_report(blotter_path, positions_path=None, now: float | None = None) -> dict:
    """Compute the G1 progress snapshot from the blotter (read-only)."""
    now = float(now if now is not None else time.time())
    records = load_records(blotter_path)
    screens = _events(records, "screen")
    entries = [e for e in _events(records, "entry") if e.get("mint")]
    exits = [e for e in _events(records, "exit") if e.get("mint")]
    blocked = _events(records, "entry_blocked")

    first_entry_ts = min((e.get("ts", 0.0) for e in entries), default=None)
    elapsed_days = ((now - first_entry_ts) / DAY_S) if first_entry_ts else 0.0

    unique_mints = len({e["mint"] for e in entries})
    per_mint_exits: Counter = Counter(e["mint"] for e in exits)
    net_usd = round(sum(float(e.get("net_usd", 0.0) or 0.0) for e in exits), 4)

    funnel: Counter = Counter()
    for s in screens:
        funnel["passed" if s.get("passed") else (s.get("rejected_by") or "unknown")] += 1

    cooldown_blocks = [b for b in blocked if b.get("reason") == "re-entry cooldown"]

    days_ok = elapsed_days >= G1_MIN_DAYS
    count_ok = len(exits) >= G1_MIN_ROUND_TRIPS
    net_ok = len(exits) > 0 and net_usd > 0.0

    report = {
        "now": now,
        "first_entry_ts": first_entry_ts,
        "elapsed_days": round(elapsed_days, 3),
        "screens": len(screens),
        "entries": len(entries),
        "unique_mints": unique_mints,
        "round_trips": len(exits),
        "net_round_trips_needed": max(0, G1_MIN_ROUND_TRIPS - len(exits)),
        "net_usd": net_usd,
        "days_needed": round(max(0.0, G1_MIN_DAYS - elapsed_days), 3),
        "round_trips_needed": max(0, G1_MIN_ROUND_TRIPS - len(exits)),
        "days_ok": days_ok,
        "count_ok": count_ok,
        "net_ok": net_ok,
        "g1_pass": bool(days_ok and count_ok and net_ok),
        "funnel": dict(funnel.most_common()),
        "cooldown_blocks": len(cooldown_blocks),
        "per_mint_exits": dict(per_mint_exits.most_common()),
    }
    if positions_path:
        report["positions_net_usd"] = _positions_net(positions_path)
    return report


def _positions_net(positions_path) -> float | None:
    try:
        data = json.loads(open(positions_path, encoding="utf-8").read())
        return round(float(data.get("realized_net_usd", 0.0)), 4)
    except (OSError, ValueError, TypeError):
        return None


def render_g1_report(rep: dict) -> str:
    """Human-readable G1 progress block."""
    lines = ["=== G1 progress (paper gate) ==="]
    if rep["first_entry_ts"] is None:
        lines.append("clock:   NOT STARTED (no entry records yet)")
    else:
        lines.append(f"clock:   {rep['elapsed_days']:.2f} / {G1_MIN_DAYS:.0f} days"
                     f"  ({rep['days_needed']:.2f} to go)")
    lines.append(f"round-trips: {rep['round_trips']} / {G1_MIN_ROUND_TRIPS}"
                 f"  ({rep['net_round_trips_needed']} to go)")
    lines.append(f"net PnL: ${rep['net_usd']:.4f}  ({'positive' if rep['net_ok'] else 'not yet positive'})")
    lines.append(f"universe: {rep['unique_mints']} unique mints entered,"
                 f" {rep['entries']} entries, {rep['screens']} screens")
    lines.append(f"cooldown blocks: {rep['cooldown_blocks']}")
    lines.append("criteria: "
                 f"days={'OK' if rep['days_ok'] else 'no'} "
                 f"count={'OK' if rep['count_ok'] else 'no'} "
                 f"net={'OK' if rep['net_ok'] else 'no'}")
    lines.append(f"G1: {'PASS — gate cleared' if rep['g1_pass'] else 'NOT MET'}")
    if rep.get("positions_net_usd") is not None:
        lines.append(f"positions.json realized net: ${rep['positions_net_usd']:.4f}")
    if rep["per_mint_exits"]:
        lines.append("round-trips per mint: "
                     + ", ".join(f"{m[:8]}={n}" for m, n in rep["per_mint_exits"].items()))
    return "\n".join(lines)
