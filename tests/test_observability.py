"""S1/S3/S4/W6 regression: observability is asserted, not grepped.

Covers: metrics naming rules + snapshot shape, heartbeat outcome vocabulary,
watchdog stall/recover transitions (once each, never per tick), the S4
transport/response split, kill-switch gauge pairing, risk-rejection counting
by closed code, ML-degradation bridging with assets stripped, and package
lifecycle transition counters.
"""
import pytest

from src import metrics
from src.metrics import (
    OUTCOME_ERROR,
    OUTCOME_NO_TRADES,
    OUTCOME_REJECTED,
    OUTCOME_TRADED,
)


@pytest.fixture(autouse=True)
def clean_metrics():
    metrics.reset()
    yield
    metrics.reset()


class TestMetricsSemantics:
    def test_counter_requires_total_suffix(self):
        with pytest.raises(AssertionError):
            metrics.inc("tars_cycles", {})

    def test_gauge_forbids_total_suffix(self):
        with pytest.raises(AssertionError):
            metrics.set_gauge("tars_something_total", 1.0)

    def test_counter_accumulates_by_label(self):
        metrics.inc("tars_decisions_total", {"direction": "LONG"})
        metrics.inc("tars_decisions_total", {"direction": "LONG"})
        metrics.inc("tars_decisions_total", {"direction": "SHORT"})
        snap = metrics.snapshot()
        assert snap["counters"]['tars_decisions_total{direction="LONG"}'] == 2
        assert snap["counters"]['tars_decisions_total{direction="SHORT"}'] == 1

    def test_gauge_overwrites_not_accumulates(self):
        metrics.set_gauge("tars_kill_switch_active", 1.0)
        metrics.set_gauge("tars_kill_switch_active", 1.0)
        assert metrics.get_gauge("tars_kill_switch_active") == 1.0

    def test_snapshot_shape(self):
        snap = metrics.snapshot()
        assert set(snap) == {"counters", "gauges", "heartbeat", "ml_degradations"}
        assert snap["heartbeat"] is None


class TestHeartbeatVocabulary:
    def test_beat_rejects_unknown_outcome(self):
        with pytest.raises(AssertionError):
            metrics.beat("c1", "moon", {}, 0)

    def test_three_way_distinction_preserved(self):
        # "no cycle ran" (None) vs "ran, no trades" vs "rejected" vs "traded"
        # must be distinct observable states, never conflated.
        assert metrics.get_heartbeat() is None
        metrics.beat("c1", OUTCOME_NO_TRADES, {"decisions": 0}, 0)
        assert metrics.get_heartbeat()["outcome"] == OUTCOME_NO_TRADES
        metrics.beat("c2", OUTCOME_REJECTED, {"decisions": 2}, 0)
        assert metrics.get_heartbeat()["outcome"] == OUTCOME_REJECTED
        metrics.beat("c3", OUTCOME_TRADED, {"executions": 1}, 0)
        assert metrics.get_heartbeat()["outcome"] == OUTCOME_TRADED
        metrics.beat("c4", OUTCOME_ERROR, {}, 3)
        hb = metrics.get_heartbeat()
        assert hb["outcome"] == OUTCOME_ERROR
        assert hb["errors"] == 3


class TestWatchdogTransitions:
    def _watchdog(self):
        from src.scheduler import LoopWatchdog
        return LoopWatchdog(expected_interval_s=900.0, grace_s=300.0)

    def test_never_started_is_silent_not_stalled(self):
        w = self._watchdog()
        assert w.check(1_000_000.0, None) is None
        assert w.stalled is False

    def test_stall_fires_once_then_recovered_once(self):
        w = self._watchdog()
        now = 1_000_000.0
        bound = w.bound_s
        assert w.check(now, now - bound - 1) == "stalled"
        assert w.stalled is True
        # Persistent condition must NOT re-fire per tick (ika rule).
        assert w.check(now + 60, now - bound - 1) is None
        assert w.check(now + 120, now - bound - 1) is None
        assert w.check(now + 180, now) == "recovered"
        assert w.stalled is False
        assert w.check(now + 240, now) is None

    def test_within_bound_never_fires(self):
        w = self._watchdog()
        now = 1_000_000.0
        assert w.check(now, now - w.bound_s + 60) is None
        assert w.stalled is False


class TestExchangeSplit:
    def test_kind_defaults_transport_and_method_label(self):
        from src.okx_cli import OkxCliError, _method_of
        cli_args = ["market", "trades", "--instId", "X"]
        err = OkxCliError(cli_args, 1, "boom")
        assert err.kind == OkxCliError.TRANSPORT
        assert _method_of(cli_args) == "market.trades"
        # Flags never leak into labels.
        assert _method_of(("trade", "order", "--instId", "BTC")) == "trade.order"

    def test_binary_missing_counts_transport(self, monkeypatch):
        import src.okx_cli as okx_cli_mod
        from src.okx_cli import OkxCli, OkxCliConfig, OkxCliError
        monkeypatch.setattr(okx_cli_mod, "_find_okx_binary", lambda: None)
        cli = OkxCli(OkxCliConfig(demo=True))

        async def _run():
            with pytest.raises(OkxCliError) as exc:
                await cli.run("market", "trades")
            assert exc.value.kind == "binary_missing"

        import asyncio
        asyncio.run(_run())
        snap = metrics.snapshot()
        assert snap["counters"]['tars_exchange_errors_total{method="market.trades"}'] == 1
        assert not [k for k in snap["counters"]
                    if k.startswith("tars_exchange_response_errors_total")]


class TestRiskAndKillSwitch:
    def test_kill_switch_pairs_trip_counter_with_gauge(self):
        from src.execution import RiskGate
        gate = RiskGate()
        gate.activate_kill_switch("test")
        assert metrics.get_gauge("tars_kill_switch_active") == 1.0
        snap = metrics.snapshot()
        assert snap["counters"]['tars_kill_switch_trips_total'] == 1
        # Second activation re-trips the counter (transition) but the gauge
        # stays put (persistent state, idempotent).
        gate.activate_kill_switch("test again")
        assert metrics.get_gauge("tars_kill_switch_active") == 1.0
        gate.deactivate_kill_switch()
        assert metrics.get_gauge("tars_kill_switch_active") == 0.0

    def test_executor_rejection_counted_by_code(self):
        from src.execution import OrderExecutor, RiskGate
        from src.execution.models import ExecutionError, OrderRequest

        class _StubCli:
            async def run(self, *args, **kwargs):
                raise AssertionError("must not reach the exchange on reject")

        gate = RiskGate(
            max_position_usd=5000,
            allowed_assets=["OTHER-USDT-SWAP"],
            allowed_companions=[],
        )
        ex = OrderExecutor(cli=_StubCli(), risk_gate=gate, dry_run=True)
        order = OrderRequest(inst_id="BTC-USDT-SWAP", side="buy",
                             order_type="market", size="10")

        async def _place():
            with pytest.raises(ExecutionError):
                await ex.place_order(order, current_price=50000.0,
                                     current_price_timestamp=1_700_000_000.0)

        import asyncio
        asyncio.run(_place())
        codes = [k for k in metrics.snapshot()["counters"]
                 if k.startswith("tars_risk_rejections_total")]
        assert len(codes) == 1
        assert sum(metrics.snapshot()["counters"][k] for k in codes) == 1


class TestMlBridge:
    def test_assets_stripped_reasons_summed(self):
        from src.signals import ML_DEGRADATIONS
        # Global counter: isolate from degradations other tests recorded
        # (e.g. real inference attempts in cycle tests fail closed here).
        ML_DEGRADATIONS.clear()
        ML_DEGRADATIONS["BTC-USDT-SWAP:ml_unavailable:RuntimeError"] = 2
        ML_DEGRADATIONS["ETH-USDT-SWAP:ml_unavailable:RuntimeError"] = 3
        ML_DEGRADATIONS["SOL-USDT-SWAP:ml_missing_prices"] = 1
        bridged = metrics.ml_degradations_bridged()
        assert bridged == {"ml_unavailable:RuntimeError": 5,
                           "ml_missing_prices": 1}
        snap = metrics.snapshot()
        assert "BTC" not in str(snap["ml_degradations"])


class TestPackageTransitions:
    def test_full_lifecycle_counts(self):
        from src.multi_leg import (
            MultiLegExecutionManager,
            PackageState,
            Step,
        )
        from src.multi_leg import LegResult
        mgr = MultiLegExecutionManager()
        steps = [
            Step(venue="a", action="short_perp", asset="BTC", amount_ratio=0.5),
            Step(venue="b", action="buy_spot", asset="BTC", amount_ratio=0.5),
        ]
        pkg = mgr.propose_package(steps, notional=10_000)

        def fill(step, notional):
            return LegResult(step=step, filled=True, fill_price=notional,
                             slippage_pct=0.001, fill_usd=notional)

        pkg = mgr.dispatch_concurrent(pkg, fill)
        assert pkg.state == PackageState.LOCKED
        mgr.settle(pkg)
        snap = metrics.snapshot()["counters"]
        assert snap['tars_packages_total{outcome="started"}'] == 1
        assert snap['tars_packages_total{outcome="locked"}'] == 1
        assert snap['tars_packages_total{outcome="settled"}'] == 1

    def test_abort_counts_closed_reason(self):
        from src.multi_leg import MultiLegExecutionManager, Step
        from src.multi_leg import LegResult
        mgr = MultiLegExecutionManager()
        steps = [
            Step(venue="a", action="short_perp", asset="BTC", amount_ratio=0.5),
            Step(venue="b", action="buy_spot", asset="BTC", amount_ratio=0.5),
        ]
        pkg = mgr.propose_package(steps, notional=10_000)

        def half(step, notional):
            if step.venue == "a":
                return LegResult(step=step, filled=True, fill_price=notional,
                                 slippage_pct=0.001, fill_usd=notional)
            return LegResult(step=step, filled=False, fill_price=None,
                             slippage_pct=None)

        pkg = mgr.dispatch_concurrent(pkg, half)

        def unwind(step, notional):
            return LegResult(step=step, filled=True, fill_price=notional,
                             slippage_pct=0.001, fill_usd=notional)

        mgr.resolve_partial_fill(pkg, unwind)
        snap = metrics.snapshot()["counters"]
        key = 'tars_packages_total{outcome="aborted",reason="partial_fill"}'
        assert snap[key] == 1


@pytest.mark.asyncio
async def test_cycle_records_heartbeat_and_counts():
    """A dry-run cycle wires outcome + heartbeat + decision/order counters
    end to end (S2: silence after this would mean 'no cycle ran')."""
    from src.agent import AutonomousTradingAgent
    from src.execution import RiskGate

    class _StubCli:
        async def run(self, *args, **kwargs):
            cmd = args[:1]
            if cmd == ("market",) and "trades" in args:
                return {"data": [{"px": "100", "sz": "1"} for _ in range(30)]}
            if cmd == ("market",) and "funding-rate" in args:
                return {"data": [{"fundingRate": "0.00001"}]}
            if cmd == ("account",):
                return {"data": []}
            return {"data": []}

    gate = RiskGate(
        max_position_usd=5000,
        allowed_assets=["BTC-USDT-SWAP"],
        allowed_companions=["BTC-USDT"],
    )
    agent = AutonomousTradingAgent(okx_cli=_StubCli(), risk_gate=gate,
                                   dry_run=True)
    result = await agent.run_trading_cycle(["BTC-USDT-SWAP"])
    hb = metrics.get_heartbeat()
    assert hb is not None
    assert hb["cycle_id"] == result.cycle_id
    assert hb["outcome"] in (OUTCOME_NO_TRADES, OUTCOME_REJECTED, OUTCOME_TRADED)
    assert hb["outcome"] != OUTCOME_ERROR
