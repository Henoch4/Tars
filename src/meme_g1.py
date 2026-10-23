"""G1 progress report — read-only measurement of the paper gate.

G1 = >=7 days of paper operation AND >=30 net-positive paper round-trips
(net-positive after the paper cost model). This module reads the append-only
blotter + positions file and reports progress toward G1. It NEVER writes,
never imports the loop, and never touches trading state.

Clock proxy: the first `entry` record's timestamp is the start of live paper
operation (the restart that began the current run). Round-trips = completed
`exit` records.

Attribution: the gate's pass/fail is a single number, which hides which exit
class is paying for it. Window-3 showed the answer matters — rug-sentinel trips
lost money while pivot trips made it — so the reporter also buckets every exit
by its reason prefix and reports net/gross/costs per class. The classes are the
same strings the loop writes, classified with the loop's own convention
(`str(reason).upper().startswith("RUG")`, see meme_loop's cooldown arming), so
the report can never disagree with the behaviour about what a rug is.
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter
from typing import TypedDict

G1_MIN_DAYS = 7.0
G1_MIN_ROUND_TRIPS = 30
DAY_S = 86400.0

# Exit-reason taxonomy, in stable reporting order. Anything unrecognised is
# counted under CLASS_OTHER — never dropped, so the class totals always sum to
# the report total (a trip that vanishes from attribution is a lie).
CLASS_RUG = "rug"
CLASS_PIVOT = "pivot"
CLASS_TIME_BOX = "time_box"
CLASS_OTHER = "other"
EXIT_CLASS_ORDER = (CLASS_RUG, CLASS_PIVOT, CLASS_TIME_BOX, CLASS_OTHER)


class ClassBucket(TypedDict):
    """One exit class's running totals (and the derived fields)."""

    exits: int
    net_usd: float
    gross_usd: float
    costs_usd: float
    wins: int
    losses: int
    unpriced: int
    worst_net_usd: float | None
    best_net_usd: float | None
    avg_net_usd: float | None
    net_share_pct: float | None


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


def _finite_field(rec: dict, key: str) -> float | None:
    """Tolerant numeric read: bool/NaN/inf/non-number are 'unpriced', not 0.

    Attribution must not invent a zero for a missing PnL — a zero pulls the
    class average toward break-even and hides exactly the losses we are
    trying to attribute."""
    value = rec.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def classify_exit_reason(reason) -> str:
    """Bucket a free-form exit reason into its attribution class.

    Prefix match on the upper-cased string, mirroring the loop's own rug test
    (`str(reason).upper().startswith("RUG")` in meme_loop's cooldown arming).
    Blank/unobserved/unknown reasons land in CLASS_OTHER."""
    text = str(reason).strip().upper() if reason is not None else ""
    if text.startswith("RUG"):
        return CLASS_RUG
    if text.startswith("PIVOT"):
        return CLASS_PIVOT
    if text.startswith("TIME BOX"):
        return CLASS_TIME_BOX
    return CLASS_OTHER


def build_attribution(exits: list) -> dict:
    """Net/gross/costs per exit class, plus the totals they decompose.

    Read-only and total-preserving: every exit lands in exactly one class, and
    each class's trip count sums to `exits`. Exits with no usable net_usd are
    counted as `unpriced` and excluded from the average (never treated as 0).
    """
    classes: dict[str, ClassBucket] = {
        name: {"exits": 0, "net_usd": 0.0, "gross_usd": 0.0, "costs_usd": 0.0,
               "wins": 0, "losses": 0, "unpriced": 0,
               "worst_net_usd": None, "best_net_usd": None,
               "avg_net_usd": None, "net_share_pct": None}
        for name in EXIT_CLASS_ORDER
    }
    total_net = 0.0
    total_gross = 0.0
    total_costs = 0.0
    unpriced_total = 0

    for e in exits:
        bucket = classes[classify_exit_reason(e.get("reason"))]
        bucket["exits"] += 1
        gross = _finite_field(e, "gross_usd")
        costs = _finite_field(e, "costs_usd")
        if gross is not None:
            bucket["gross_usd"] += gross
            total_gross += gross
        if costs is not None:
            bucket["costs_usd"] += costs
            total_costs += costs
        net = _finite_field(e, "net_usd")
        if net is None:
            bucket["unpriced"] += 1
            unpriced_total += 1
            continue
        bucket["net_usd"] += net
        total_net += net
        if net > 0:
            bucket["wins"] += 1
        elif net < 0:
            bucket["losses"] += 1
        worst, best = bucket["worst_net_usd"], bucket["best_net_usd"]
        bucket["worst_net_usd"] = net if worst is None else min(worst, net)
        bucket["best_net_usd"] = net if best is None else max(best, net)

    for bucket in classes.values():
        priced = bucket["exits"] - bucket["unpriced"]
        raw_net = bucket["net_usd"]
        bucket["avg_net_usd"] = round(raw_net / priced, 4) if priced else None
        bucket["net_share_pct"] = (round(raw_net / total_net * 100.0, 1)
                                   if total_net else None)
        bucket["net_usd"] = round(raw_net, 4)
        bucket["gross_usd"] = round(bucket["gross_usd"], 4)
        bucket["costs_usd"] = round(bucket["costs_usd"], 4)

    return {
        "exits": len(exits),
        "priced_exits": len(exits) - unpriced_total,
        "unpriced_exits": unpriced_total,
        "net_usd": round(total_net, 4),
        "gross_usd": round(total_gross, 4),
        "costs_usd": round(total_costs, 4),
        "classes": {name: classes[name] for name in EXIT_CLASS_ORDER
                    if classes[name]["exits"]},
    }


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
        "attribution": build_attribution(exits),
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


def render_attribution(attr: dict) -> str:
    """Human-readable per-class attribution block."""
    lines = ["=== G1 exit attribution ==="]
    if not attr.get("exits"):
        lines.append("no completed round-trips yet")
        return "\n".join(lines)
    lines.append(f"exits: {attr['exits']} (priced {attr['priced_exits']},"
                 f" unpriced {attr['unpriced_exits']})"
                 f"  net ${attr['net_usd']:+.4f}"
                 f"  gross ${attr['gross_usd']:+.4f}"
                 f"  costs ${attr['costs_usd']:.4f}")
    for name, b in attr["classes"].items():
        avg = "n/a" if b["avg_net_usd"] is None else f"${b['avg_net_usd']:+.2f}"
        share = ("" if b["net_share_pct"] is None
                 else f"  share {b['net_share_pct']:+.1f}%")
        lines.append(f"  {name:<9} {b['exits']:>3} trips"
                     f"  net ${b['net_usd']:+.4f}  avg {avg}"
                     f"  {b['wins']}W/{b['losses']}L{share}")
    if attr.get("unpriced_exits"):
        lines.append("  (unpriced exits are counted, excluded from avg —"
                     " missing net is never read as break-even)")
    return "\n".join(lines)


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
    if rep.get("attribution"):
        lines.append(render_attribution(rep["attribution"]))
    return "\n".join(lines)
