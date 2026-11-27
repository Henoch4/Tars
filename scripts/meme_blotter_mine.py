"""Solana blotter miner — offline analysis of G1 window data.

Reads the VPS blotter (copied locally) and answers the research questions
that feed restart #5/#6 decisions. Read-only; does not touch the live loop.

Usage:
    python scripts/meme_blotter_mine.py [path/to/blotter.jsonl]

Outputs:
  - Outcome class distribution (rug / pivot / time_box / other)
  - Hold-time distributions by class (rugs exit faster — sentinel containment)
  - Screen rejection mix (which gates are doing the work)
  - Worst/best individual trades
  - Gap report: which context fields are NOT yet in the blotter (deployed
    at restart #5 via feat/entry-context + feat/bundle-measurement)
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def join_entries_to_exits(rows: list[dict]) -> list[dict]:
    """For every exit, the most recent preceding entry of the same mint."""
    last_entry: dict[str, dict] = {}
    joined = []
    for r in sorted(rows, key=lambda r: r.get("ts") or 0.0):
        mint = r.get("mint")
        if r.get("event") == "entry" and isinstance(mint, str):
            last_entry[mint] = r
        elif r.get("event") == "exit" and isinstance(mint, str) and mint in last_entry:
            e = last_entry[mint]
            joined.append({
                "mint": mint,
                "net_usd": r.get("net_usd"),
                "reason": r.get("reason") or "",
                "entry_ts": e.get("ts"),
                "exit_ts": r.get("ts"),
                "hold_time_s": r.get("hold_time_s"),
                "entry_size_usd": e.get("size_usd"),
                "entry_price": e.get("price"),
                "exit_price": r.get("exit_price"),
            })
    return joined


def classify(reason: str) -> str:
    r = str(reason or "").upper()
    if r.startswith("RUG"):
        return "rug"
    if r.startswith("PIVOT"):
        return "pivot"
    if r.startswith("TIME"):
        return "time_box"
    return "other"


def fmt(v, f="{:.2f}") -> str:
    if v is None:
        return "n/a"
    return f.format(v)


def stats(vals: list) -> str:
    if not vals:
        return "n/a"
    s = sorted(vals)
    n = len(s)
    return (f"n={n} sum={sum(s):+9.2f} mean={sum(s)/n:+8.2f} "
            f"med={s[n//2]:+8.2f} min={s[0]:+8.2f} max={s[-1]:+8.2f}")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    path = Path(argv[0]) if argv else Path("blotter.jsonl")
    if not path.exists():
        print(f"no blotter at {path}")
        return 0

    rows = load_rows(path)
    joined = join_entries_to_exits(rows)

    screens = [r for r in rows if r.get("event") == "screen"]
    entries = [r for r in rows if r.get("event") == "entry"]
    exits = [r for r in rows if r.get("event") == "exit"]
    blocks = [r for r in rows if r.get("event") == "entry_blocked"]

    print(f"=== Solana blotter mine: {path.name} ===")
    print(f"rows: {len(rows)} | screens: {len(screens)} | entries: {len(entries)} | "
          f"exits: {len(exits)} | blocks: {len(blocks)} | joined pairs: {len(joined)}")
    print()

    # --- Class distribution ---
    classes: Counter = Counter()
    nets: dict[str, list] = defaultdict(list)
    for j in joined:
        c = classify(j["reason"])
        classes[c] += 1
        if isinstance(j["net_usd"], (int, float)):
            nets[c].append(j["net_usd"])

    print("--- outcome class distribution ---")
    for c, n in classes.most_common():
        net = nets.get(c, [])
        print(f"  {c:10s} n={n:3d} | {stats(net)}")
    print()

    # --- Hold time by class ---
    hold: dict[str, list] = defaultdict(list)
    for j in joined:
        c = classify(j["reason"])
        h = j.get("hold_time_s")
        if isinstance(h, (int, float)):
            hold[c].append(float(h))

    print("--- hold time (seconds) by class ---")
    for c in ("rug", "pivot", "time_box", "other"):
        if hold.get(c):
            print(f"  {c:10s} {stats(hold[c])}")
    print()

    # --- Screen rejection mix ---
    rej: Counter = Counter()
    for s in screens:
        rej[s.get("rejected_by") or "unknown"] += 1
    print("--- screen rejection mix (gates doing the work) ---")
    for k, n in rej.most_common():
        print(f"  {k:30s} {n:5d}")
    print()

    # --- Worst rugs / best pivots ---
    rugs = [j for j in joined if classify(j["reason"]) == "rug"
            and isinstance(j["net_usd"], (int, float))]
    pivots = [j for j in joined if classify(j["reason"]) == "pivot"
              and isinstance(j["net_usd"], (int, float))]
    rugs.sort(key=lambda j: j["net_usd"])
    pivots.sort(key=lambda j: j["net_usd"], reverse=True)

    print("--- worst 5 rugs ---")
    for j in rugs[:5]:
        print(f"  {j['mint'][:14]}... net {j['net_usd']:+8.2f} "
              f"hold {j.get('hold_time_s'):.0f}s | {j['reason'][:70]}")
    print()

    print("--- best 5 pivots ---")
    for j in pivots[:5]:
        print(f"  {j['mint'][:14]}... net {j['net_usd']:+8.2f} "
              f"hold {j.get('hold_time_s'):.0f}s | {j['reason'][:70]}")
    print()

    # --- Gap report: what's NOT in the blotter (deployed at restart #5) ---
    entry_fields: Counter = Counter()
    for r in entries:
        entry_fields.update(r.keys())
    screen_fields: Counter = Counter()
    for r in screens:
        screen_fields.update(r.keys())

    expected_entry_ctx = ("price_change", "txns_windows", "fdv_usd", "age_s",
                          "mcap_usd", "bundle_cluster_pct", "bundle_complete")
    expected_screen_ctx = ("creator_address", "rugcheck_rugged", "price_change",
                           "bundle_cluster_pct", "bundle_complete")

    print("--- gap report: context fields missing from blotter (deploy restart #5) ---")
    for f in expected_entry_ctx:
        present = f in entry_fields
        print(f"  entry.{f:24s} {'PRESENT' if present else 'MISSING (deploy feat/entry-context)'}")
    for f in expected_screen_ctx:
        present = f in screen_fields
        print(f"  screen.{f:22s} {'PRESENT' if present else 'MISSING (deploy feat/entry-context/bundle)'}")
    print()

    # --- Concentration data gap ---
    has_concentration = any("top10_hold_pct" in r or "creator_hold_pct" in r
                            for r in entries + screens)
    print(f"  concentration fields (top10/creator hold): "
          f"{'PRESENT' if has_concentration else 'MISSING — not persisted to blotter'}")
    print()
    print("  IMPLICATION: Q1 (dev_mints clustering), Q2 (concentration by class), "
          "Q3 (h6 run-position), Q5 (bundle share) cannot be answered from "
          "this blotter. They require the entry-context + bundle captures "
          "deployed at restart #5 (Oct 30).")
    print()
    print("--- what IS answerable from this window ---")
    print("  Q4: hold time — rugs exit faster than pivots (sentinel containment working)")
    print("  Rejection mix — which gates filter most candidates (unconfirmed dominates)")
    print("  Per-trade net — individual rug/pivot outcomes for attribution")
    print()
    print("=== end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
