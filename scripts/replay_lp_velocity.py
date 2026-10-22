"""Offline replay: would a one-cycle LP-velocity confirmation have helped?

Reads a LOCAL copy of the satellite blotter (never the live VPS file) and
for every entered mint asks the honest counterfactual from the module in
src/meme_lp_velocity.py. The pre-entry window is blind by construction
(pass -> enter on the same cycle), so the replayed gate is the
confirmation-stage design: enter one cycle LATE, observe the pool again,
reject if the liquidity drain measured across the span crosses the
threshold. A rejection means "we never took the trade" — the mint's
realized net is removed from the portfolio.

Counterfactual net caveat, stated up front: for a trade we would not
have taken, the counterfactual is zero PnL from that mint. For a trade
we WOULD still have taken, we keep its realized net. The replay cannot
know what would have filled the freed capital — same stated limit as the
meme_shadow gates.

Usage: py -3 scripts/replay_lp_velocity.py path/to/blotter.jsonl
"""
from __future__ import annotations

import json
import math
import re
import sys
from typing import Any

sys.path.insert(0, ".")
from src.meme_lp_velocity import (  # noqa: E402
    DEFAULT_DROP_PCT_PER_MIN,
    LPSample,
    liquidity_verdict,
)

_POOL_RE = re.compile(r"pool \$([\d,]+)")
CONFIRM_MAX_DELAY_S = 2760.0  # ~9 cycles; beyond that the "second look" is a different trade
DEFAULT_MAX_DELAY_S = 915.0  # one cycle (~5 min) + cadence slack


def _pool_usd_from_reasons(reasons, notes) -> float | None:
    for note in reasons or []:
        m = _POOL_RE.search(str(note))
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except (TypeError, ValueError):
                notes.append(f"unparsable pool note: {note!r}")
    return None


def _finite(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return float(value)


def fold_blotter(rows: list) -> dict:
    """Per-mint LP samples and realized net for entered mints."""
    mints: dict = {}
    for r in rows:
        mint = r.get("mint")
        if not isinstance(mint, str) or not mint:
            continue
        state = mints.setdefault(mint, {
            "mint": mint, "samples": [], "entered": False,
            "entry_ts": None, "entry_liq": None, "exit": None,
        })
        ev = r.get("event")
        ts = _finite(r.get("ts"))
        if ts is not None:
            src = r.get("sized_from_liquidity_usd") \
                if ev == "entry" else _pool_usd_from_reasons(r.get("reasons"), [])
            liq = _finite(src)
            if liq is not None:
                state["samples"].append(LPSample(ts, liq))
        if ev == "entry":
            state["entered"] = True
            state["entry_ts"] = _finite(r.get("ts"))
            state["entry_liq"] = _finite(r.get("sized_from_liquidity_usd"))
        elif ev == "exit":
            net = _finite(r.get("net_usd"))
            reason = r.get("reason")
            state["exit"] = {"net_usd": net or 0.0, "reason": str(reason or "")}
    return mints


def _confirmation_samples(mint_state: dict, max_delay_s: float):
    """Samples inside the confirmation window after the entry row."""
    t_entry = mint_state["entry_ts"]
    liq_entry = mint_state["entry_liq"]
    if t_entry is None or liq_entry is None:
        return None
    window = [LPSample(t_entry, liq_entry)] + [
        s for s in mint_state["samples"]
        if t_entry < s.ts <= t_entry + max_delay_s
    ]
    return window or None


def replay(mints: dict, thresholds=(2.0, 4.0, 8.0, 15.0, 25.0, 40.0),
           max_delay_s: float = DEFAULT_MAX_DELAY_S, armed: bool = True) -> dict:
    entered = {m: s for m, s in mints.items() if s["entered"]}
    actual_net = sum(s["exit"]["net_usd"] if s["exit"] else 0.0 for s in entered.values())
    coverage: dict[str, float] = {m: (s["exit"]["net_usd"] if s["exit"] else 0.0)
                                  for m, s in entered.items()}
    report: dict[str, Any] = {
        "entered": len(entered), "max_delay_s": max_delay_s,
        "actual_net_usd": round(actual_net, 2),
        "thresholds": [], "uncoverable": [],
    }
    for m, s in entered.items():
        window = _confirmation_samples(s, max_delay_s)
        vv = liquidity_verdict(m, window, armed=armed) if window else None
        if vv is None or vv.action == "insufficient":
            report["uncoverable"].append({
                "mint": m, "net_usd": round(coverage[m], 2)})
    for th in thresholds:
        kept_net = 0.0
        rug_loss_avoided = 0.0
        winner_killed = 0.0
        per_mint: list[tuple[str, str, float | None, float]] = []
        for m, s in entered.items():
            window = _confirmation_samples(s, max_delay_s)
            vv = liquidity_verdict(m, window,
                                   threshold_pct_per_min=th,
                                   armed=armed) if window else None
            net = coverage[m]
            if vv is None or vv.action == "insufficient":
                kept_net += net  # fail-open for the replay table: unmeasurable stays kept
                per_mint.append((m, "missing", None, round(net, 2)))
            elif vv.action == "reject":
                if net < 0:
                    rug_loss_avoided += -net
                    per_mint.append((m, "REJECT", vv.drop_pct_per_min, round(net, 2)))
                else:
                    winner_killed += net
                    per_mint.append((m, "reject_winner", vv.drop_pct_per_min, round(net, 2)))
            else:
                kept_net += net
                per_mint.append((m, "kept", vv.drop_pct_per_min, round(net, 2)))
        report["thresholds"].append({
            "threshold": th,
            "kept_net_usd": round(kept_net, 2),
            "delta_vs_actual": round(kept_net - actual_net, 2),
            "rug_loss_avoided_usd": round(rug_loss_avoided, 2),
            "winner_killed_usd": round(winner_killed, 2),
            "per_mint": per_mint,
        })
    return report


def render(report: dict) -> str:
    out = [f"=== LP-velocity confirmation replay (max delay {report['max_delay_s']:.0f}s) ===",
           f"entered mints: {report['entered']}; actual net: ${report['actual_net_usd']:,.2f}",
           f"uncoverable (no in-window second look, kept open): "
           f"{len(report['uncoverable'])} mints, net "
           f"${sum(u['net_usd'] for u in report['uncoverable']):,.2f}",
           f"{'thr%/min':>9} {'kept$':>10} {'delta$':>9} {'loss-avoided$':>14} "
           f"{'winner-killed$':>15}"]
    for t in report["thresholds"]:
        out.append(f"{t['threshold']:>9.0f} {t['kept_net_usd']:>10.2f} "
                   f"{t['delta_vs_actual']:>9.2f} {t['rug_loss_avoided_usd']:>14.2f} "
                   f"{t['winner_killed_usd']:>15.2f}")
    return "\n".join(out)


def main(argv: list) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) != 2:
        print("usage: py -3 scripts/replay_lp_velocity.py blotter.jsonl")
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    mints = fold_blotter(rows)
    for delay in (DEFAULT_MAX_DELAY_S, CONFIRM_MAX_DELAY_S):
        report = replay(mints, max_delay_s=delay)
        print(render(report))
        print()
        best = report["thresholds"]
        for t in best:
            kills = [pm for pm in t["per_mint"] if pm[1] in ("REJECT", "reject_winner")]
            losers_killed = sum(1 for pm in kills if pm[1] == "REJECT")
            winners_killed = sum(1 for pm in kills if pm[1] == "reject_winner")
            print(f"thr {t['threshold']:>2.0f}%/min: avoided {losers_killed} rug-losses"
                  f" (${t['rug_loss_avoided_usd']:,.2f}), killed {winners_killed} "
                  f"winners (${t['winner_killed_usd']:,.2f}) → net "
                  f"{t['delta_vs_actual']:+,.2f}")
        print("-" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
