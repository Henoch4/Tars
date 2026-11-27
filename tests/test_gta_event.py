from datetime import datetime, timezone
import time

from src.gta_event import GtaEventPolicy, evaluate_candidate, evaluate_exit


def _policy(**overrides):
    base = dict(
        enabled=True,
        start_utc=datetime(2026, 11, 18, tzinfo=timezone.utc),
        end_utc=datetime(2026, 11, 22, tzinfo=timezone.utc),
        max_total_usd=100.0,
        max_per_token_usd=10.0,
        max_open_positions=3,
    )
    base.update(overrides)
    return GtaEventPolicy(**base)


def _candidate(**overrides):
    candidate = {
        "mint": "mint-1",
        "name": "GTA VI Survivor",
        "symbol": "GTA6",
        "size_usd": 10.0,
        "liquidity_usd": 150.0,
        "top10_hold_pct": 12.0,
        "holder_count": 250,
        "mint_authority_disabled": True,
        "freeze_authority_disabled": True,
        "honeypot": False,
        "transfer_tax_pct": 0.0,
        "bundled_buy_pct": 10.0,
    }
    candidate.update(overrides)
    return candidate


def test_policy_is_disabled_by_default():
    decision = evaluate_candidate(GtaEventPolicy(), _candidate(), now=datetime(2026, 11, 19, tzinfo=timezone.utc))
    assert decision.allowed is False
    assert decision.reason == "policy disabled"


def test_live_arm_requires_both_flags_and_positive_limits():
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    assert _policy().broadcast_allowed(now) == (False, "live policy disabled")
    armed = _policy(live_enabled=True)
    assert armed.broadcast_allowed(now) == (True, "live window and budgets are armed")
    assert _policy(live_enabled=True, max_total_usd=0).broadcast_allowed(now)[0] is False


def test_exit_policy_fires_stop_and_take_profit_ladder():
    policy = _policy(live_enabled=True)
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    entry_ts = time.time()
    stop = evaluate_exit(policy, entry_price=100, peak_price=100, entry_ts=entry_ts,
                          current_price=64, now=now)
    assert stop.action == "sell_all"
    assert "stop-loss" in stop.reason
    take = evaluate_exit(policy, entry_price=100, peak_price=220, entry_ts=entry_ts,
                         current_price=205, now=now, ladder_fired=(False, False))
    assert take.action == "sell_partial"
    assert take.sell_pct == 45


def test_candidate_passes_only_inside_window_with_all_hard_checks():
    decision = evaluate_candidate(_policy(), _candidate(), now=datetime(2026, 11, 19, tzinfo=timezone.utc))
    assert decision.allowed is True


def test_missing_or_unsafe_data_fails_closed():
    candidate = _candidate(top10_hold_pct=None, honeypot=None)
    decision = evaluate_candidate(_policy(), candidate, now=datetime(2026, 11, 19, tzinfo=timezone.utc))
    assert decision.allowed is False
    assert "missing hard-filter data" == decision.reason


def test_budget_and_window_caps_are_hard():
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    assert evaluate_candidate(_policy(), _candidate(size_usd=11), now=now).allowed is False
    assert evaluate_candidate(_policy(), _candidate(), spent_usd=95, now=now).allowed is False
    assert evaluate_candidate(
        _policy(), _candidate(),
        now=datetime(2026, 11, 17, 23, 59, 59, tzinfo=timezone.utc),
    ).allowed is False
