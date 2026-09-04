"""Unit tests for src/multi_leg.py — atomic multi-leg execution.

No deps, no network. Run: pytest tests/test_multi_leg.py -v
"""
import pytest

from src.multi_leg import (
    MultiLegExecutionManager,
    PaperFillSimulator,
    Step,
    LegResult,
    PackageState,
    validate_steps,
)


def two_leg_steps():
    return [
        Step(venue="venue_a", action="short_perp", asset="BTC", amount_ratio=0.5),
        Step(venue="venue_b", action="buy_spot", asset="BTC", amount_ratio=0.5),
    ]


def always_fill(step, notional):
    return LegResult(step=step, filled=True, fill_price=notional, slippage_pct=0.001)


def test_validate_steps_requires_ratios_sum_to_one():
    bad_steps = [
        Step(venue="venue_a", action="short_perp", asset="BTC", amount_ratio=0.4),
        Step(venue="venue_b", action="buy_spot", asset="BTC", amount_ratio=0.4),
    ]
    errors = validate_steps(bad_steps)
    assert any("sum to 1.0" in e for e in errors)


def test_validate_steps_rejects_implausible_slippage():
    bad = [
        Step(venue="a", action="short_perp", asset="BTC", amount_ratio=0.5, max_slippage_pct=0.9),
        Step(venue="b", action="buy_spot", asset="BTC", amount_ratio=0.5, max_slippage_pct=0.003),
    ]
    errors = validate_steps(bad)
    assert any("implausible max_slippage_pct" in e for e in errors)


def test_both_legs_fill_reaches_locked_then_settled():
    mgr = MultiLegExecutionManager()
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    pkg = mgr.dispatch_concurrent(pkg, always_fill)
    assert pkg.state == PackageState.LOCKED
    assert pkg.slippage_breached is False

    pkg = mgr.settle(pkg)
    assert pkg.state == PackageState.SETTLED
    assert mgr.open_package_count() == 0


def test_dispatch_unwinds_on_slippage_breach():
    """Regression for the source bug: max_slippage_pct was stored on the step
    but never enforced in the dispatch path, so a 5x-slippage fill still
    counted as a clean locked fill. It must flag the breach and refuse LOCKED."""
    mgr = MultiLegExecutionManager()
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    def breach_on_second_leg(step, notional):
        breached = step.venue == "venue_b"
        return LegResult(
            step=step,
            filled=True,
            fill_price=notional,
            slippage_pct=0.05 if breached else 0.001,
        )

    pkg = mgr.dispatch_concurrent(pkg, breach_on_second_leg)
    assert pkg.slippage_breached is True
    assert pkg.state == PackageState.PENDING_FILL  # never LOCKED on a bad fill

    unwind_calls = []
    def unwind_sim(step, notional):
        unwind_calls.append((step.action, notional))
        return LegResult(step=step, filled=True, fill_price=notional, slippage_pct=0.001)

    pkg = mgr.resolve_slippage_breach(pkg, unwind_sim)
    assert pkg.state == PackageState.ABORTED
    assert pkg.unwound is True
    assert {a for a, _ in unwind_calls} == {"cover_perp", "sell_spot"}  # both legs unwound
    assert mgr.open_package_count() == 0


def test_dispatch_within_slippage_is_not_a_breach():
    mgr = MultiLegExecutionManager()
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    def at_limit(step, notional):
        return LegResult(step=step, filled=True, fill_price=notional,
                         slippage_pct=step.max_slippage_pct)  # exactly at limit: allowed

    pkg = mgr.dispatch_concurrent(pkg, at_limit)
    assert pkg.slippage_breached is False
    assert pkg.state == PackageState.LOCKED


def test_breach_on_unfilled_leg_is_ignored():
    # A leg that did not fill has no realized slippage; it must not trip the breach.
    mgr = MultiLegExecutionManager()
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    def one_no_fill(step, notional):
        if step.venue == "venue_b":
            return LegResult(step=step, filled=False, fill_price=None, slippage_pct=None)
        return LegResult(step=step, filled=True, fill_price=notional, slippage_pct=0.001)

    pkg = mgr.dispatch_concurrent(pkg, one_no_fill)
    assert pkg.slippage_breached is False  # partial fill, not slippage breach
    assert pkg.state == PackageState.PENDING_FILL


def test_partial_fill_triggers_automatic_unwind():
    mgr = MultiLegExecutionManager()
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    call_count = {"n": 0}

    def one_fills_one_doesnt(step, notional):
        call_count["n"] += 1
        filled = call_count["n"] == 1
        return LegResult(step=step, filled=filled,
                         fill_price=notional if filled else None,
                         slippage_pct=0.001 if filled else None)

    pkg = mgr.dispatch_concurrent(pkg, one_fills_one_doesnt)
    assert pkg.state == PackageState.PENDING_FILL  # not all filled

    unwind_calls = []

    def unwind_sim(step, notional):
        unwind_calls.append((step.action, notional))
        return LegResult(step=step, filled=True, fill_price=notional, slippage_pct=0.001)

    pkg = mgr.resolve_partial_fill(pkg, unwind_sim)
    assert pkg.state == PackageState.ABORTED
    assert pkg.unwound is True
    assert len(unwind_calls) == 1  # only the filled leg gets unwound
    assert mgr.open_package_count() == 0  # released, not left dangling


def test_no_fill_at_all_is_clean_abort_without_unwind():
    mgr = MultiLegExecutionManager()
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    def never_fill(step, notional):
        return LegResult(step=step, filled=False, fill_price=None, slippage_pct=None)

    pkg = mgr.dispatch_concurrent(pkg, never_fill)

    unwind_calls = []

    def unwind_sim(step, notional):
        unwind_calls.append(step)
        return LegResult(step=step, filled=True, fill_price=notional, slippage_pct=0.001)

    pkg = mgr.resolve_partial_fill(pkg, unwind_sim)
    assert pkg.state == PackageState.ABORTED
    assert pkg.unwound is False
    assert len(unwind_calls) == 0


def test_unwind_failure_never_marks_package_unwound():
    """Regression: resolvers set pkg.unwound unconditionally, so even a failed
    unwind order (rejected at the exchange, network timeout, kill-switch block)
    was recorded as 'safely unwound' while real exposure remained. unwound=True
    now requires every unwind leg to report a real fill."""
    mgr = MultiLegExecutionManager()
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    def one_fills_one_doesnt(step, notional):
        filled = step.venue == "venue_a"
        return LegResult(step=step, filled=filled,
                         fill_price=notional if filled else None,
                         slippage_pct=0.001 if filled else None)

    pkg = mgr.dispatch_concurrent(pkg, one_fills_one_doesnt)

    # The unwind attempt fails: the cancellation order is rejected by the venue.
    def failed_unwind(step, notional):
        return LegResult(step=step, filled=False, fill_price=None, slippage_pct=None)

    pkg = mgr.resolve_partial_fill(pkg, failed_unwind)
    assert pkg.state == PackageState.ABORTED
    assert pkg.unwound is False  # naked exposure remains; must NOT claim safe


def test_slippage_breach_unwind_failure_keeps_unwound_false():
    mgr = MultiLegExecutionManager()
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    def breach_all(step, notional):
        return LegResult(step=step, filled=True, fill_price=notional,
                         slippage_pct=0.05)  # 5% >> 0.3% collar on every leg

    pkg = mgr.dispatch_concurrent(pkg, breach_all)
    assert pkg.slippage_breached is True

    def failed_unwind(step, notional):
        return LegResult(step=step, filled=False, fill_price=None, slippage_pct=None)

    pkg = mgr.resolve_slippage_breach(pkg, failed_unwind)
    assert pkg.state == PackageState.ABORTED
    assert pkg.unwound is False


def test_duplication_check_blocks_second_package_same_asset():
    mgr = MultiLegExecutionManager()
    mgr.propose_package(two_leg_steps(), notional=10_000)

    can_open, reason = mgr.can_open("BTC")
    assert can_open is False
    assert "duplication" in reason


def test_capacity_check_blocks_beyond_max_concurrent():
    mgr = MultiLegExecutionManager(max_concurrent_packages=1)
    mgr.propose_package(two_leg_steps(), notional=10_000)

    other_asset_steps = [
        Step(venue="venue_a", action="short_perp", asset="ETH", amount_ratio=0.5),
        Step(venue="venue_b", action="buy_spot", asset="ETH", amount_ratio=0.5),
    ]
    can_open, reason = mgr.can_open("ETH")
    assert can_open is False
    assert "capacity" in reason


def test_propose_raises_on_invalid_steps():
    mgr = MultiLegExecutionManager()
    with pytest.raises(ValueError):
        mgr.propose_package([two_leg_steps()[0]], notional=10_000)


def test_resolve_routes_slippage_breach_before_fill_state():
    """Regression: `resolve()` must route on slippage_breached BEFORE fill
    state. A package whose legs all filled but one breached its slippage
    collar must unwind fail-closed — the old partial-fill-first ordering
    would treat 'all legs filled' as a clean LOCKED trade and leave the
    breached leg's exposure in place."""
    mgr = MultiLegExecutionManager()
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    def breach_on_second_leg(step, notional):
        breached = step.venue == "venue_b"
        return LegResult(
            step=step,
            filled=True,
            fill_price=notional,
            slippage_pct=0.05 if breached else 0.001,
        )

    pkg = mgr.dispatch_concurrent(pkg, breach_on_second_leg)
    assert pkg.slippage_breached is True
    assert all(r.filled for r in pkg.leg_results)  # all filled, still breached

    unwind_calls = []

    def unwind_sim(step, notional):
        unwind_calls.append((step.action, notional))
        return LegResult(step=step, filled=True, fill_price=notional, slippage_pct=0.001)

    pkg = mgr.resolve(pkg, unwind_sim)
    assert pkg.state == PackageState.ABORTED
    assert pkg.unwound is True
    assert {a for a, _ in unwind_calls} == {"cover_perp", "sell_spot"}
    assert mgr.open_package_count() == 0


def test_resolve_routes_partial_fill_when_no_slippage_breach():
    """A package that is partially filled but not slippage-breached routes to
    the partial-fill path (only the filled leg unwinds)."""
    mgr = MultiLegExecutionManager()
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    call_count = {"n": 0}

    def one_fills_one_doesnt(step, notional):
        call_count["n"] += 1
        filled = call_count["n"] == 1
        return LegResult(
            step=step, filled=filled,
            fill_price=notional if filled else None,
            slippage_pct=0.001 if filled else None,
        )

    pkg = mgr.dispatch_concurrent(pkg, one_fills_one_doesnt)
    assert pkg.slippage_breached is False

    unwind_calls = []

    def unwind_sim(step, notional):
        unwind_calls.append((step.action, notional))
        return LegResult(step=step, filled=True, fill_price=notional, slippage_pct=0.001)

    pkg = mgr.resolve(pkg, unwind_sim)
    assert pkg.state == PackageState.ABORTED
    assert len(unwind_calls) == 1  # only the filled leg
    assert mgr.open_package_count() == 0


def test_resolve_leaves_locked_package_untouched():
    """resolve() on an already-LOCKED package (all filled, no breach) is a
    no-op — the routing must not re-enter a resolver or unwind anything."""
    mgr = MultiLegExecutionManager()
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)
    pkg = mgr.dispatch_concurrent(pkg, always_fill)
    assert pkg.state == PackageState.LOCKED

    called = []

    def unwind_sim(step, notional):
        called.append(step)
        return LegResult(step=step, filled=True, fill_price=notional, slippage_pct=0.001)

    resolved = mgr.resolve(pkg, unwind_sim)
    assert resolved is pkg
    assert resolved.state == PackageState.LOCKED
    assert called == []


def test_close_package_uses_symmetric_inverse():
    mgr = MultiLegExecutionManager()
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    pkg = mgr.dispatch_concurrent(pkg, always_fill)
    pkg = mgr.settle(pkg)

    closed = mgr.close_package(pkg, always_fill)
    closing_actions = [r.step.action for r in closed.leg_results[2:]]
    # original actions were short_perp, buy_spot -> inverse+reversed should be sell_spot, cover_perp
    assert closing_actions == ["sell_spot", "cover_perp"]


def test_z2_read_ok_not_truthiness_declined_leg_not_filled():
    """Z2 regression: a declined/refused leg must be recorded as NOT filled,
    never as filled due to truthiness of the result object.

    Zinger's bug: `!!(await executeTrade(...))` treated a refusal object
    `{ ok: false, error: '...' }` as truthy, so a declined leg was marked
    filled and the package locked with naked exposure. Our code checks
    explicit `filled` attribute — this test pins that a result object that
    would be truthy in a boolean context but has `filled=False` is NEVER
    treated as filled.
    """
    mgr = MultiLegExecutionManager()
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    # A "refusal object" that would be truthy in `if result:` but means failure.
    # Our code MUST check `leg_result.filled`, not `if leg_result:`
    def refusal_object_leg_1(step, notional):
        # This object is truthy (has attrs, not None) but filled=False
        return LegResult(
            step=step,
            filled=False,
            fill_price=None,
            slippage_pct=None,
            fill_usd=None,
        )

    def normal_fill_leg_2(step, notional):
        return LegResult(
            step=step,
            filled=True,
            fill_price=notional,
            slippage_pct=0.001,
        )

    # Dispatch: leg 1 returns a "truthy refusal", leg 2 fills normally
    # If the code checked truthiness, leg 1 would count as filled and package LOCKED
    pkg = mgr.dispatch_concurrent(pkg, lambda step, n: (
        refusal_object_leg_1(step, n) if step.venue == "venue_a" else normal_fill_leg_2(step, n)
    ))

    # Must NOT lock: leg 1 explicitly filled=False
    assert pkg.state == PackageState.PENDING_FILL
    assert pkg.leg_results[0].filled is False  # explicitly not filled
    assert pkg.leg_results[1].filled is True   # explicitly filled

    # Unwind: only leg 2 (the actually filled one) gets unwound
    unwind_calls = []
    def unwind_sim(step, notional):
        unwind_calls.append((step.action, notional))
        return LegResult(step=step, filled=True, fill_price=notional, slippage_pct=0.001)

    pkg = mgr.resolve_partial_fill(pkg, unwind_sim)
    assert pkg.state == PackageState.ABORTED
    assert len(unwind_calls) == 1  # ONLY the actually-filled leg unwound
    assert unwind_calls[0][0] == "sell_spot"  # inverse of buy_spot


def test_amount_aware_unwind_scales_by_fill_ratio():
    """D1(a) regression: the unwind must scale to the ACTUAL filled amount,
    not the full leg notional. Old code unwound the other legs at full
    notional while only a fraction of the broken leg existed — over-closing
    the hedge and leaving reverse exposure."""
    import tempfile

    mgr = MultiLegExecutionManager(persist_dir=tempfile.mkdtemp(prefix="ml_d1a_"))
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    def half_fill_first_leg(step, notional):
        if step.venue == "venue_a":
            # Half fill: 2500 of 5000 leg notional
            return LegResult(step=step, filled=True, fill_price=notional,
                             slippage_pct=0.001, fill_usd=2500.0)
        return LegResult(step=step, filled=False, fill_price=None, slippage_pct=None)

    pkg = mgr.dispatch_concurrent(pkg, half_fill_first_leg)
    assert pkg.state == PackageState.PENDING_FILL
    assert pkg.leg_results[0].fill_ratio == pytest.approx(0.5)

    unwind_calls = []

    def unwind_sim(step, notional):
        unwind_calls.append((step.action, notional))
        return LegResult(step=step, filled=True, fill_price=notional, slippage_pct=0.001)

    pkg = mgr.resolve_partial_fill(pkg, unwind_sim)
    assert pkg.state == PackageState.ABORTED
    assert pkg.unwound is True
    assert len(unwind_calls) == 1
    # Unwind scaled to the filled fraction (2500), NOT the full leg (5000)
    assert unwind_calls[0][1] == pytest.approx(2500.0)


def test_crash_recovery_restores_pending_package():
    """Z3/W4 regression: a restart mid-package must restore the persisted
    PENDING_FILL package (min-age interlock defers reconciliation of a
    recent fill) instead of leaving amnesia — and the asset must stay
    blocked while the package is unrestored."""
    import tempfile

    persist = tempfile.mkdtemp(prefix="ml_recover_")
    mgr = MultiLegExecutionManager(persist_dir=persist)
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)

    def one_fills(step, notional):
        filled = step.venue == "venue_a"
        return LegResult(step=step, filled=filled,
                         fill_price=notional if filled else None,
                         slippage_pct=0.001 if filled else None)

    pkg = mgr.dispatch_concurrent(pkg, one_fills)
    assert pkg.state == PackageState.PENDING_FILL
    pkg_id = pkg.id

    # "Crash": brand-new manager on the same persist dir
    mgr2 = MultiLegExecutionManager(persist_dir=persist)
    assert mgr2.open_package_count() == 1
    can_open, reason = mgr2.can_open("BTC")
    assert can_open is False
    assert "duplication" in reason
    restored = mgr2._open_packages[pkg_id]
    assert restored.state == PackageState.PENDING_FILL
    assert len(restored.leg_results) == 2


def test_recovery_cleans_terminal_package_files():
    """Terminal-state scratch files must not accumulate: a persisted ABORTED
    package older than the min-age interlock is deleted on recovery."""
    import json
    import os
    import tempfile
    import time

    persist = tempfile.mkdtemp(prefix="ml_cleanup_")
    mgr = MultiLegExecutionManager(persist_dir=persist)
    pkg = mgr.propose_package(two_leg_steps(), notional=10_000)
    # Simulate an old terminal record left on disk
    path = os.path.join(persist, f"pkg_{pkg.id}.json")
    with open(path, "w") as f:
        json.dump({
            "id": pkg.id,
            "steps": [{"venue": s.venue, "action": s.action, "asset": s.asset,
                       "amount_ratio": s.amount_ratio,
                       "max_slippage_pct": s.max_slippage_pct} for s in pkg.steps],
            "notional": pkg.notional,
            "state": "aborted",
            "leg_results": [],
            "unwound": False,
            "slippage_breached": False,
            "created_at": time.time() - 3600,
            "updated_at": time.time() - 3600,
            "dispatched_legs": 0,
            "last_fill_ts": time.time() - 3600,
        }, f)

    mgr2 = MultiLegExecutionManager(persist_dir=persist)
    assert mgr2.open_package_count() == 0
    assert not os.path.exists(path)


def test_paper_fill_simulator_can_actually_breach_slippage():
    """Regression for the cap bug: the source clamped slippage with min(..., 
    max*1.5), so no fill could ever exceed max_slippage_pct and the breach path
    was dead code. The ported simulator must occasionally produce a true breach."""
    step = Step(venue="a", action="short_perp", asset="BTC", amount_ratio=1.0, max_slippage_pct=0.003)
    sim = PaperFillSimulator(seed=7, fill_prob=1.0)
    breaches = 0
    for _ in range(10_000):
        result = sim(step, 1_000)
        assert result.filled
        if result.slippage_pct > step.max_slippage_pct:
            breaches += 1
    assert breaches > 0  # occasionally a fill at up to several sigma slippage
    assert breaches < 5_000  # but not so often it is always breached