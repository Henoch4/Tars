#!/usr/bin/env python3
"""tars-paperz report — analysis-plan angles for the paper cohort.

Reads the harness state/ledger/audit files and prints:
  §0 reconciliation        ledger vs state, net carry tie-out
  §2.1 carry decomposition funding vs fees vs slippage vs price move (primary)
  §2.2 z dose-response     avg net PnL by entry z bucket (primary)
  §2.3 regime dependence   BTC drawdown-from-90d-high at entry
  §2.4 hold-time profile   hold periods vs the 21-period cap
  §2.5 cost realism        realized slippage vs 3 bp/side model
  §2.6 loss clustering     longest loss streak, gap between worst trades
  §2.7 signal availability cycle counts by rationale type

Hermetic: reads only on-disk JSON/JSONL. No network, no keys, no trades.
Usage: python scripts/paperz_report.py [data_dir]
   data_dir defaults to <repo>/data (where paper_trade_sol.py writes).
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.paper_trade_sol import (  # noqa: E402
    LEDGER_PATH, STATE_PATH, AUDIT_PATH, FEE_BPS, SLIP_BPS,
    MAX_TRADES, MAX_HOLD_PERIODS, PERIOD_S,
)


def _load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def slippage_cost_usd(trade: dict) -> float:
    """Realized slippage cost = size * exit_slippage_bps / 1e4 (one leg)."""
    return trade["size_usd"] * trade["exit_slippage_bps"] / 1e4


def carry_decomposition(trades: list) -> dict:
    """§2.1 — npnl = funding − entry_fee − exit_fee − slippage + price_move."""
    funding = fees = slip = price = 0.0
    for t in trades:
        funding += t["funding_accrued_usd"]
        fees += t["entry_fee_usd"] + t["exit_fee_usd"]
        slip += slippage_cost_usd(t)
        price += t["gross_pnl_usd"]
    return {
        "funding_collected_usd": round(funding, 4),
        "fees_usd": round(fees, 4),
        "slippage_usd": round(slip, 4),
        "price_move_usd": round(price, 4),
        "net_pnl_usd": round(funding - fees - slip + price, 4),
    }


def z_buckets(trades: list) -> dict:
    """§2.2 — avg net PnL by entry z bucket (monotonic dose-response check)."""
    buckets = defaultdict(list)
    for t in trades:
        z = (t.get("signal_meta") or {}).get("funding_z")
        if z is None:
            continue
        if z >= 2.5:
            b = ">=2.5"
        elif z >= 2.0:
            b = "2.0-2.5"
        elif z >= 1.5:
            b = "1.5-2.0"
        else:
            b = "<1.5"
        buckets[b].append(t["net_pnl_usd"])
    out = {}
    for b in (">=2.5", "2.0-2.5", "1.5-2.0", "<1.5"):
        vals = buckets.get(b, [])
        out[b] = {"n": len(vals),
                  "avg_net_pnl_usd": round(sum(vals) / len(vals), 4) if vals else None}
    return out


def regime_dependence(trades: list) -> dict:
    """§2.3 — cohort performance by BTC drawdown-from-90d-high at entry."""
    groups = defaultdict(list)
    missing = 0
    for t in trades:
        tag = (t.get("signal_meta") or {}).get("btc_regime_tag") or {}
        dd = tag.get("btc_drawdown_from_90d_high")
        if dd is None:
            missing += 1
            continue
        if dd <= -0.10:
            b = "drawdown >= 10%"
        elif dd <= -0.05:
            b = "drawdown 5-10%"
        else:
            b = "within 5% of high"
        groups[b].append(t["net_pnl_usd"])
    out = {}
    for b in ("drawdown >= 10%", "drawdown 5-10%", "within 5% of high"):
        vals = groups.get(b, [])
        out[b] = {"n": len(vals),
                  "avg_net_pnl_usd": round(sum(vals) / len(vals), 4) if vals else None,
                  "win_rate": round(sum(1 for v in vals if v > 0) / len(vals), 4) if vals else None}
    out["missing_tag"] = missing
    return out


def hold_time_profile(trades: list) -> dict:
    """§2.4 — hold periods vs the MAX_HOLD_PERIODS cap."""
    at_cap = [t for t in trades if t["hold_periods"] >= MAX_HOLD_PERIODS - 0.01]
    return {
        "n_at_cap": len(at_cap),
        "avg_hold_periods": round(sum(t["hold_periods"] for t in trades) / len(trades), 2) if trades else 0,
        "max_hold_periods": round(max((t["hold_periods"] for t in trades), default=0), 2),
    }


def cost_realism(trades: list) -> dict:
    """§2.5 — realized slippage vs 3 bp/side model (model is per side, round trip ~6bp)."""
    slips = [t["exit_slippage_bps"] for t in trades]
    over_2x = sum(1 for s in slips if s > SLIP_BPS * 2)
    return {
        "mean_exit_slippage_bps": round(sum(slips) / len(slips), 2) if slips else 0,
        "max_exit_slippage_bps": round(max(slips, default=0), 2),
        "trades_over_2x_model": over_2x,
        "model_slip_bps_per_side": SLIP_BPS,
    }


def _ts_to_epoch(ts: str) -> float:
    from datetime import datetime
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return 0.0


def loss_clustering(trades: list) -> dict:
    """§2.6 — longest loss streak, calendar gap between two worst trades."""
    longest = cur = 0
    for t in trades:
        if t["net_pnl_usd"] < 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    worst_trades = sorted(trades, key=lambda t: t["net_pnl_usd"])[:2]
    gap = None
    if len(worst_trades) == 2:
        ts1 = worst_trades[0].get("closed_at", "")
        ts2 = worst_trades[1].get("closed_at", "")
        if ts1 and ts2:
            gap = abs(_ts_to_epoch(ts1) - _ts_to_epoch(ts2))
    return {
        "longest_loss_streak": longest,
        "worst_trade_net_usd": round(worst_trades[0]["net_pnl_usd"], 4) if worst_trades else None,
        "second_worst_net_usd": round(worst_trades[1]["net_pnl_usd"], 4) if len(worst_trades) > 1 else None,
        "gap_between_worst_seconds": gap,
    }


def signal_availability(audit: list) -> dict:
    """§2.7 — how often the edge fires, by rationale type."""
    reasons = Counter()
    total = 0
    for rec in audit:
        if rec.get("event") != "cycle":
            continue
        total += 1
        rationale = rec.get("rationale") or rec.get("signal", {}).get("rationale", "unknown")
        reasons[rationale] += 1
    return {"cycles": total, "by_rationale": dict(reasons)}


def reconcile(state: dict, ledger: list) -> dict:
    """§0 — ledger vs state tie-out."""
    opens = [r for r in ledger if r.get("event") == "open"]
    closes = [r for r in ledger if r.get("event") == "close"]
    closed_trades = state.get("closed_trades", [])
    open_pos = state.get("open_position")
    net_carry = state.get("net_carry_usd", 0.0)
    sum_closed = round(sum(t["net_pnl_usd"] for t in closed_trades), 4)
    opens_without_close = len(opens) - len(closes)
    return {
        "ledger_opens": len(opens),
        "ledger_closes": len(closes),
        "closed_trades_in_state": len(closed_trades),
        "live_open_position": open_pos is not None,
        "net_carry_usd_state": round(net_carry, 4),
        "sum_closed_net_pnl_usd": sum_closed,
        "reconciliation_drift_usd": round(net_carry - sum_closed, 4),
        "opens_without_close": opens_without_close,
    }


def main() -> int:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "data"
    state_path = data_dir / "paper_sol_state.json"
    ledger_path = data_dir / "paper_sol_ledger.jsonl"
    audit_path = data_dir / "paper_sol_audit.jsonl"

    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    ledger = _load_jsonl(ledger_path)
    audit = _load_jsonl(audit_path)
    trades = state.get("closed_trades", [])

    print("=" * 64)
    print("tars-paperz report — funding_persistence_z paper cohort")
    print("=" * 64)

    print("\n§0 Reconciliation (ledger vs state)")
    rec = reconcile(state, ledger)
    for k, v in rec.items():
        print(f"  {k}: {v}")
    ok = rec["reconciliation_drift_usd"] == 0.0 and rec["opens_without_close"] == 0
    print(f"  RECONCILED: {ok}")

    print(f"\n§2.1 Carry decomposition (primary) — {len(trades)} closed trades")
    cd = carry_decomposition(trades)
    for k, v in cd.items():
        print(f"  {k}: {v}")

    print(f"\n§2.2 Z dose-response (primary)")
    for b, d in z_buckets(trades).items():
        print(f"  z {b}: n={d['n']} avg_net_pnl=${d['avg_net_pnl_usd']}")

    print(f"\n§2.3 Regime dependence (BTC drawdown from 90d high)")
    rd = regime_dependence(trades)
    for b, d in rd.items():
        if b == "missing_tag":
            print(f"  missing BTC tag: {d}")
        else:
            print(f"  {b}: n={d['n']} avg_net_pnl=${d['avg_net_pnl_usd']} win_rate={d['win_rate']}")

    print(f"\n§2.4 Hold-time profile (cap = {MAX_HOLD_PERIODS} periods)")
    hp = hold_time_profile(trades)
    for k, v in hp.items():
        print(f"  {k}: {v}")

    print(f"\n§2.5 Cost realism audit (model {SLIP_BPS} bp/side)")
    cr = cost_realism(trades)
    for k, v in cr.items():
        print(f"  {k}: {v}")

    print(f"\n§2.6 Loss clustering")
    lc = loss_clustering(trades)
    for k, v in lc.items():
        print(f"  {k}: {v}")

    print(f"\n§2.7 Signal availability")
    sa = signal_availability(audit)
    print(f"  cycles observed: {sa['cycles']}")
    for rat, n in sa["by_rationale"].items():
        print(f"  {rat}: {n}")

    print(f"\nSuccess gate: trades={len(trades)}/{MAX_TRADES} "
          f"net_carry=${state.get('net_carry_usd', 0):.2f} "
          f"reconciled={ok}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
