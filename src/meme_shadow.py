"""Offline shadow-filter analysis — replay the blotter, measure a hypothetical gate.

Answers "what would an extra gate have done?" WITHOUT touching the running
loop (the loop's behavior must stay frozen while G1 is measured). Read-only.

Scope + honest limits: the blotter records `organic_score` (and `top10_hold_pct`)
only as free-text notes on rows that reached the END of the filter — i.e. passes.
Rejected rows bail early and carry no such note. So a shadow gate can only be
evaluated on the tokens we actually PASSED; it cannot see what it would have
rejected upstream. That is exactly the right question for "would gating on this
have improved the trades we took?" and is stated in the output.
"""
from __future__ import annotations

import re

_ORGANIC_RE = re.compile(r"jupiter organic score ([-\d.]+)")
_TOP10_RE = re.compile(r"top10 holds ([\d.]+)%")

METRIC_PATTERNS = {
    "organic_score": _ORGANIC_RE,
    "top10_hold_pct": _TOP10_RE,
}


def _metric_from_reasons(reasons, key: str):
    pat = METRIC_PATTERNS[key]
    for note in (reasons or []):
        m = pat.search(str(note))
        if m:
            try:
                return float(m.group(1))
            except (TypeError, ValueError):
                return None
    return None


def per_mint_facts(records: list) -> dict:
    """Fold blotter records into per-mint facts: metric values (from pass
    notes), entry sizes, and realized net per mint."""
    facts: dict = {}
    for r in records:
        mint = r.get("mint")
        if not isinstance(mint, str) or not mint:
            continue
        f = facts.setdefault(mint, {
            "mint": mint, "organic_score": None, "top10_hold_pct": None,
            "passed": False, "entered": False, "exits": [], "net_usd": 0.0,
        })
        ev = r.get("event")
        if ev == "screen":
            if r.get("passed"):
                f["passed"] = True
                score = _metric_from_reasons(r.get("reasons"), "organic_score")
                if score is not None and f["organic_score"] is None:
                    f["organic_score"] = score
                top10 = _metric_from_reasons(r.get("reasons"), "top10_hold_pct")
                if top10 is not None and f["top10_hold_pct"] is None:
                    f["top10_hold_pct"] = top10
        elif ev == "entry":
            f["entered"] = True
        elif ev == "exit":
            f["exits"].append(r)
            f["net_usd"] += float(r.get("net_usd", 0.0) or 0.0)
    return facts


def shadow_gate(facts: dict, key: str, threshold: float,
                drop_missing: bool = False) -> dict:
    """Hypothetical gate `facts[mint][key] >= threshold` on ENTERTED mints.

    drop_missing=True also skips entered mints with no reading (fail-closed),
    matching the repo's SKIP-default doctrine. Returns the kept net vs the
    actual net, and the skipped mints with their realized net."""
    entered = {m: f for m, f in facts.items() if f["entered"]}
    actual_net = round(sum(f["net_usd"] for f in entered.values()), 4)
    kept_net = 0.0
    skipped = []
    for mint, f in entered.items():
        val = f.get(key)
        if val is None:
            if drop_missing:
                skipped.append({"mint": mint, "value": None, "net_usd": round(f["net_usd"], 4)})
                continue
            kept_net += f["net_usd"]
            continue
        if val < threshold:
            skipped.append({"mint": mint, "value": val, "net_usd": round(f["net_usd"], 4)})
        else:
            kept_net += f["net_usd"]
    return {
        "key": key,
        "threshold": threshold,
        "drop_missing": drop_missing,
        "entered": len(entered),
        "kept": len(entered) - len(skipped),
        "actual_net_usd": actual_net,
        "hypothetical_net_usd": round(kept_net, 4),
        "delta_usd": round(kept_net - actual_net, 4),
        "skipped": sorted(skipped, key=lambda s: s["net_usd"]),
    }


def sweep(facts: dict, key: str, thresholds, drop_missing: bool = False) -> dict:
    """Shadow a range of thresholds. Reports coverage: how many entered mints
    actually carry the metric (a low count means the data source is too sparse
    for the gate to be meaningful)."""
    entered = {m: f for m, f in facts.items() if f["entered"]}
    with_metric = sum(1 for f in entered.values() if f.get(key) is not None)
    results = [shadow_gate(facts, key, t, drop_missing) for t in thresholds]
    return {
        "key": key,
        "entered": len(entered),
        "entered_with_metric": with_metric,
        "coverage_pct": round(100.0 * with_metric / len(entered), 1) if entered else 0.0,
        "results": results,
    }


def render_sweep(rep: dict) -> str:
    lines = [f"=== shadow gate: {rep['key']} ===",
             f"entered {rep['entered']} mints; {rep['entered_with_metric']} carry "
             f"the metric ({rep['coverage_pct']}% coverage)"]
    if rep["entered_with_metric"] == 0:
        lines.append("insufficient data: no entered mint carries this metric "
                     "(the note only lands on rows that reach the filter's end)")
        return "\n".join(lines)
    lines.append(f"{'threshold':>9} {'kept':>5} {'actual$':>10} {'shadow$':>10} {'delta$':>9}")
    for r in rep["results"]:
        lines.append(f"{r['threshold']:>9.1f} {r['kept']:>5d} "
                     f"{r['actual_net_usd']:>10.4f} {r['hypothetical_net_usd']:>10.4f} "
                     f"{r['delta_usd']:>9.4f}")
    return "\n".join(lines)
