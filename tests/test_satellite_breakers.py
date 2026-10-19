"""Satellite breaker tests — pure, deterministic, no clock."""
from src.satellite_breakers import (BreakerLimits, SatelliteBreakers, day_key,
                                    _end_of_day)

T = 1_700_000_000.0
DAY = 86400.0


def _b(**kw):
    return SatelliteBreakers(limits=BreakerLimits(**kw))


def test_loss_accounting_and_streak_reset():
    b = _b()
    b.record_result(-10.0, T)
    assert b.daily_loss[day_key(T)] == 10.0
    assert b.consecutive_losses == 1
    b.record_result(2.0, T)
    assert b.consecutive_losses == 0  # a win resets the streak
    assert b.daily_loss[day_key(T)] == 10.0  # but not the day's loss


def test_garbage_inputs_ignored():
    b = _b()
    for bad in (True, "x", float("nan"), None):
        b.record_result(bad, T)
    assert b.daily_loss == {} and b.consecutive_losses == 0


def test_daily_cap_pauses_until_next_day():
    b = _b(max_daily_loss_usd=50.0, drawdown_loss_mult=10.0)
    b.record_result(-30.0, T)
    allowed, _ = b.new_entries_allowed(0, 0.0, T)
    assert allowed is True
    b.record_result(-25.0, T)  # 55 >= 50
    allowed, reason = b.new_entries_allowed(0, 0.0, T)
    assert allowed is False and "paused" in reason
    # next day: pause expired (unless streak/halt)
    allowed, _ = b.new_entries_allowed(0, 0.0, _end_of_day(T) + 1)
    assert allowed is True


def test_streak_triggers_cooldown():
    b = _b(max_consecutive_losses=3, streak_cooldown_s=3600.0, max_daily_loss_usd=1000.0)
    for _ in range(3):
        b.record_result(-1.0, T)
    assert b.paused_until == T + 3600.0
    allowed, reason = b.new_entries_allowed(0, 0.0, T + 1)
    assert allowed is False


def test_trailing_drawdown_hard_halts():
    b = _b(max_daily_loss_usd=50.0, drawdown_loss_mult=2.0)
    b.record_result(-105.0, T)  # >= 2*50
    assert b.halted is True
    allowed, reason = b.new_entries_allowed(0, 0.0, _end_of_day(T) + 100)
    assert allowed is False and "halted" in reason
    b.reset()
    assert b.new_entries_allowed(0, 0.0, _end_of_day(T) + 100)[0] is True


def test_max_open_and_heat():
    b = _b(max_open_positions=2, max_portfolio_heat_usd=50.0)
    assert b.new_entries_allowed(2, 0.0, T) == (False, "max open positions (2)")
    allowed, reason = b.new_entries_allowed(0, 50.0, T)
    assert allowed is False and "heat" in reason
    assert b.new_entries_allowed(1, 10.0, T)[0] is True


def test_fail_closed_on_unreadable_exposure():
    b = _b()
    assert b.new_entries_allowed(0, None, T)[0] is False
    assert b.new_entries_allowed(None, 0.0, T)[0] is False


def test_state_roundtrip():
    b = _b()
    b.record_result(-7.0, T)
    b.record_result(-3.0, T)
    state = b.state()
    b2 = SatelliteBreakers.from_state(state)
    assert b2.daily_loss[day_key(T)] == 10.0
    assert b2.consecutive_losses == 2
    # garbage state -> clean defaults, never raises
    b3 = SatelliteBreakers.from_state("nope")
    assert b3.consecutive_losses == 0
