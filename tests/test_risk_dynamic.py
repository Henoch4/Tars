"""I8 regression: cooldown-after-loss, trailing drawdown trip, graded score.

Cooldown blocks new entries after any reported loss (unwind exempt,
expires on schedule, disabled at 0, durable across restarts). Trailing
drawdown trips the kill switch on multi-day bleeding the intraday limit
misses, derived from durable day entries. The graded score is advisory
only — it never gates.
"""
import time

import pytest

from src.execution import RiskGate
from src.execution.models import ExecutionError, OrderRequest


def _fresh_ts() -> float:
    return time.time()


def _order(**overrides):
    kwargs = dict(
        inst_id="BTC-USDT-SWAP",
        side="buy",
        order_type="market",
        size="100",
        confidence_bps=8000,
        leverage=2.0,
    )
    kwargs.update(overrides)
    return OrderRequest(**kwargs)


def _check(gate, order=None, agent="a1"):
    return gate.check_order(
        order or _order(), agent,
        current_price=50000.0,
        current_price_timestamp=_fresh_ts(),
    )


class TestCooldown:
    def test_blocks_entries_after_loss(self):
        gate = RiskGate(loss_cooldown_minutes=30.0)
        assert _check(gate).approved is True
        gate.report_loss("a1", 10.0)
        result = _check(gate)
        assert result.approved is False
        assert result.code == "COOLDOWN_ACTIVE"
        assert "remaining" in result.reason

    def test_expires_on_schedule(self):
        gate = RiskGate(loss_cooldown_minutes=30.0)
        gate.report_loss("a1", 10.0)
        assert _check(gate).approved is False
        gate._last_loss_ts -= 31 * 60  # 31 min ago
        assert _check(gate).approved is True

    def test_disabled_at_zero(self):
        gate = RiskGate(loss_cooldown_minutes=0)
        gate.report_loss("a1", 10.0)
        assert _check(gate).approved is True

    def test_unwind_exempt(self):
        gate = RiskGate(loss_cooldown_minutes=30.0)
        gate.report_loss("a1", 10.0)
        order = _order(unwind=True)
        assert gate.check_order(
            order, "a1", current_price=50000.0,
            current_price_timestamp=_fresh_ts(), unwind=True,
        ).approved is True

    def test_zero_loss_starts_no_cooldown(self):
        gate = RiskGate(loss_cooldown_minutes=30.0)
        gate.report_loss("a1", 0.0)
        assert gate.cooldown_remaining_s() == 0.0
        assert _check(gate).approved is True

    def test_persists_across_restart(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="risk_cooldown_")
        path = f"{d}/risk_state.json"
        from src.execution.risk_gate import DurableDailyCounters
        store = DurableDailyCounters(path=path, enabled=True)
        gate = RiskGate(loss_cooldown_minutes=30.0, counter_store=store)
        gate.report_loss("a1", 10.0)
        assert gate.cooldown_remaining_s() > 0

        store2 = DurableDailyCounters(path=path, enabled=True)
        gate2 = RiskGate(loss_cooldown_minutes=30.0, counter_store=store2)
        assert gate2.cooldown_remaining_s() > 0
        assert _check(gate2).approved is False


class TestTrailingDrawdown:
    def _bleed(self, gate, agent="a1", days=("2026-01-01", "2026-01-02", "2026-01-03"),
               each=400.0):
        for day in days:
            gate.report_loss(agent, each, day_key=f"{agent}:{day}")

    def test_trips_on_multiday_bleed(self):
        gate = RiskGate(max_daily_loss_usd=500, drawdown_window_days=3,
                        drawdown_loss_mult=2.0)
        self._bleed(gate)  # 3 x $400 = $1200 >= 2x$500, never tripped intraday
        assert gate.kill_switch_status()["active"] is False
        # Cooldown (0b) correctly fires first — expire it to reach the
        # drawdown check (0c) under test.
        gate._last_loss_ts -= 31 * 60
        result = _check(gate)
        assert result.approved is False
        assert result.code == "DRAWDOWN_BREACH"
        assert gate.kill_switch_status()["active"] is True

    def test_no_trip_on_clean_history(self):
        gate = RiskGate(max_daily_loss_usd=500)
        assert _check(gate).approved is True
        assert gate.kill_switch_status()["active"] is False

    def test_no_trip_below_bar(self):
        # Cooldown disabled to isolate the drawdown check.
        gate = RiskGate(max_daily_loss_usd=500, drawdown_window_days=3,
                        drawdown_loss_mult=2.0, loss_cooldown_minutes=0)
        self._bleed(gate, each=100.0)  # $300 < $1000 bar
        assert _check(gate).approved is True

    def test_disabled_at_zero_window(self):
        gate = RiskGate(max_daily_loss_usd=500, drawdown_window_days=0,
                        drawdown_loss_mult=2.0)
        self._bleed(gate, each=10000.0)
        # Intraday trip may still fire, but never the drawdown path.
        result = _check(gate)
        assert result.code != "DRAWDOWN_BREACH"

    def test_streak_breaks_on_clean_day(self):
        gate = RiskGate()
        gate.report_loss("a1", 100.0, day_key="a1:2026-01-01")
        gate.report_loss("a1", 100.0, day_key="a1:2026-01-02")
        stats = gate.trailing_loss_stats("a1", window_days=5)
        assert stats["streak_days"] == 2
        assert stats["window_loss"] == pytest.approx(200.0)

    def test_unwind_admitted_but_trip_fires(self):
        gate = RiskGate(max_daily_loss_usd=500)
        self._bleed(gate)
        order = _order(unwind=True)
        result = gate.check_order(
            order, "a1", current_price=50000.0,
            current_price_timestamp=_fresh_ts(), unwind=True)
        assert result.approved is True  # unwind admitted...
        assert gate.kill_switch_status()["active"] is True  # ...but halt set


class TestRiskScore:
    def test_fresh_gate_proceeds(self):
        gate = RiskGate()
        score = gate.score_order(_order(), "a1")
        assert score.recommendation == "PROCEED"
        assert score.score < 0.4

    def test_risk_monotone_in_loss(self):
        gate = RiskGate(max_daily_loss_usd=1000)
        low = gate.score_order(_order(), "a1").score
        gate.report_loss("a1", 600.0)
        high = gate.score_order(_order(), "a1").score
        assert high > low

    def test_wait_on_combined_pressure(self):
        # 60% intraday (0.18) + trailing 1200/2000 (0.15) + active
        # cooldown (0.15) + mid confidence (~0.10) ≈ 0.58 -> WAIT, while
        # the binary gate still approves once the cooldown expires
        # (intraday below trip, trailing below bar).
        gate = RiskGate(max_daily_loss_usd=1000, drawdown_window_days=3,
                        drawdown_loss_mult=2.0)
        gate.report_loss("a1", 600.0)
        gate.report_loss("a1", 600.0, day_key="a1:2026-01-01")
        gate.report_loss("a1", 600.0, day_key="a1:2026-01-02")
        score = gate.score_order(_order(leverage=None), "a1")
        assert score.recommendation == "WAIT"
        assert score.reasons, "WAIT without reasons is unactionable"
        gate._last_loss_ts -= 31 * 60  # expire cooldown for the gate check
        assert _check(gate).approved is True  # advisory never gates

    def test_halt_forces_block(self):
        gate = RiskGate()
        gate.activate_kill_switch("test")
        score = gate.score_order(_order(), "a1")
        assert score.score == 1.0
        assert score.recommendation == "BLOCK"

    def test_unknown_fields_noted_not_hidden(self):
        gate = RiskGate()
        order = _order(leverage=None, confidence_bps=None)
        score = gate.score_order(order, "a1")
        assert any("undeclared" in r for r in score.reasons)
