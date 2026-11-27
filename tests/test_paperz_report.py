"""tars-paperz report tests — hermetic, synthetic data only."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.paperz_report import (  # noqa: E402
    carry_decomposition, cost_realism, loss_clustering,
    hold_time_profile, regime_dependence, reconcile, signal_availability,
    z_buckets,
)


def _trade(net, funding=0.3, fee=0.0125, slip=3.0, gross=0.0, z=1.8,
           hold=3.0, btc_dd=-0.08, reason="target"):
    return {
        "closed_at": "2026-11-01 10:00:00", "reason": reason,
        "direction": "SHORT", "size_usd": 25.0, "entry_price": 100.0,
        "exit_price": 100.0, "entry_fee_usd": fee, "exit_fee_usd": fee,
        "exit_slippage_bps": slip, "funding_accrued_usd": funding,
        "gross_pnl_usd": gross, "net_pnl_usd": net,
        "hold_periods": hold,
        "signal_meta": {"funding_z": z, "persistence": 4,
                        "btc_regime_tag": {"btc_drawdown_from_90d_high": btc_dd}},
    }


def test_carry_decomposition_adds_up():
    trades = [
        _trade(net=1.0, funding=0.5, gross=1.0),
        _trade(net=-2.0, funding=0.2, gross=-2.0, slip=6.0),
    ]
    d = carry_decomposition(trades)
    assert d["funding_collected_usd"] == 0.7
    assert d["price_move_usd"] == -1.0
    assert d["fees_usd"] == 0.05
    assert d["slippage_usd"] == pytest.approx(25.0 * 6.0 / 1e4 + 25.0 * 3.0 / 1e4)
    assert d["net_pnl_usd"] == pytest.approx(0.7 - 0.05 - d["slippage_usd"] - 1.0)


def test_carry_decomposition_empty():
    assert carry_decomposition([]) == {
        "funding_collected_usd": 0.0, "fees_usd": 0.0,
        "slippage_usd": 0.0, "price_move_usd": 0.0, "net_pnl_usd": 0.0,
    }


def test_z_buckets_monotonic():
    trades = [
        _trade(net=1.0, z=1.6),   # 1.5-2.0
        _trade(net=2.0, z=2.2),   # 2.0-2.5
        _trade(net=3.0, z=2.8),   # >=2.5
        _trade(net=-5.0, z=1.2),  # <1.5
    ]
    b = z_buckets(trades)
    assert b[">=2.5"]["n"] == 1 and b[">=2.5"]["avg_net_pnl_usd"] == 3.0
    assert b["2.0-2.5"]["n"] == 1 and b["2.0-2.5"]["avg_net_pnl_usd"] == 2.0
    assert b["1.5-2.0"]["n"] == 1 and b["1.5-2.0"]["avg_net_pnl_usd"] == 1.0
    assert b["<1.5"]["n"] == 1 and b["<1.5"]["avg_net_pnl_usd"] == -5.0


def test_z_buckets_missing_z_excluded():
    trades = [_trade(net=1.0)]
    trades[0]["signal_meta"] = {}  # no funding_z
    b = z_buckets(trades)
    assert all(d["n"] == 0 for d in b.values())


def test_regime_dependence_groups():
    trades = [
        _trade(net=1.0, btc_dd=-0.12),   # drawdown >= 10%
        _trade(net=2.0, btc_dd=-0.07),   # drawdown 5-10%
        _trade(net=3.0, btc_dd=-0.02),   # within 5% of high
        _trade(net=4.0, btc_dd=None),    # missing
    ]
    r = regime_dependence(trades)
    assert r["drawdown >= 10%"]["n"] == 1
    assert r["drawdown >= 10%"]["avg_net_pnl_usd"] == 1.0
    assert r["drawdown 5-10%"]["avg_net_pnl_usd"] == 2.0
    assert r["within 5% of high"]["avg_net_pnl_usd"] == 3.0
    assert r["missing_tag"] == 1


def test_hold_time_profile_cap():
    trades = [
        _trade(net=1.0, hold=21.0),   # at cap
        _trade(net=-1.0, hold=3.0),
    ]
    h = hold_time_profile(trades)
    assert h["n_at_cap"] == 1
    assert h["max_hold_periods"] == 21.0


def test_cost_realism_over_2x():
    trades = [_trade(net=1.0, slip=7.0), _trade(net=-1.0, slip=2.0)]
    c = cost_realism(trades)
    assert c["mean_exit_slippage_bps"] == 4.5
    assert c["max_exit_slippage_bps"] == 7.0
    assert c["trades_over_2x_model"] == 1   # 7 > 6 (2x model)
    assert c["model_slip_bps_per_side"] == 3.0


def test_loss_clustering_streak_and_gap():
    trades = [
        dict(_trade(net=-1.0), closed_at="2026-11-01 10:00:00"),
        dict(_trade(net=-2.0), closed_at="2026-11-01 12:00:00"),  # streak continues
        dict(_trade(net=3.0), closed_at="2026-11-01 14:00:00"),   # streak breaks
        dict(_trade(net=-5.0), closed_at="2026-11-01 16:00:00"),  # worst
        dict(_trade(net=-10.0), closed_at="2026-11-01 18:00:00"), # second worst
    ]
    lc = loss_clustering(trades)
    assert lc["longest_loss_streak"] == 2
    assert lc["worst_trade_net_usd"] == -10.0
    assert lc["second_worst_net_usd"] == -5.0
    # gap between 16:00 and 18:00 = 7200 seconds
    assert lc["gap_between_worst_seconds"] == 7200.0


def test_loss_clustering_empty():
    assert loss_clustering([]) == {
        "longest_loss_streak": 0,
        "worst_trade_net_usd": None,
        "second_worst_net_usd": None,
        "gap_between_worst_seconds": None,
    }


def test_signal_availability_counts():
    audit = [
        {"event": "cycle", "rationale": "persistent-band"},
        {"event": "cycle", "rationale": "persistent-band"},
        {"event": "cycle", "rationale": "insufficient-history"},
        {"event": "not-a-cycle", "rationale": "persistent-band"},  # ignored
    ]
    s = signal_availability(audit)
    assert s["cycles"] == 3
    assert s["by_rationale"]["persistent-band"] == 2
    assert s["by_rationale"]["insufficient-history"] == 1


def test_reconcile_clean():
    state = {"closed_trades": [_trade(net=1.0), _trade(net=-2.0)],
             "net_carry_usd": -1.0, "open_position": None}
    ledger = [
        {"event": "open", "net_pnl_usd": 0},
        {"event": "close", "net_pnl_usd": 1.0},
        {"event": "open", "net_pnl_usd": 0},
        {"event": "close", "net_pnl_usd": -2.0},
    ]
    r = reconcile(state, ledger)
    assert r["reconciliation_drift_usd"] == 0.0
    assert r["opens_without_close"] == 0
    assert r["RECONCILED"] if "RECONCILED" in r else True


def test_reconcile_detects_drift():
    state = {"closed_trades": [_trade(net=1.0)],
             "net_carry_usd": 5.0,   # drift: state says 5, sum of closed is 1
             "open_position": None}
    ledger = [
        {"event": "open", "net_pnl_usd": 0},
        {"event": "close", "net_pnl_usd": 1.0},
    ]
    r = reconcile(state, ledger)
    assert r["reconciliation_drift_usd"] == 4.0


def test_reconcile_open_without_close():
    state = {"closed_trades": [], "net_carry_usd": 0.0, "open_position": None}
    ledger = [{"event": "open", "net_pnl_usd": 0}]  # no matching close
    r = reconcile(state, ledger)
    assert r["opens_without_close"] == 1
    assert r["ledger_opens"] == 1
    assert r["ledger_closes"] == 0
