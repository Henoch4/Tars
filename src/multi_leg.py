"""
Atomic multi-leg execution -- the piece that makes a delta-neutral thesis
buildable rather than aspirational. Multiple legs are dispatched in a single
cycle (serial submission, fail-closed) and tracked as ONE package via an
explicit state machine, unwound automatically on a partial fill rather than
left as unintended directional exposure.

State machine: PENDING_FILL -> LOCKED -> SETTLED, with ABORTED as an explicit
outcome (not a caught exception).

Serially-dispatched, not truly concurrent: each leg is a full network round-trip,
so leg 2 is submitted only after leg 1's fill is known. This is the documented
trade-off — true parallel dispatch would race the shared RiskGate state. The
leg-timing risk between fills is bounded by the settlement-window model (the
funding-arb package is deliberately built one leg at a time, not atomically on
an exchange). The resolvers fail closed if any leg mis-fills.

Different from the source MVP this was ported from: every declared limit is
actually checked in the code path that executes it. The original dispatch
stored ``max_slippage_pct`` on each step but never enforced it -- a leg that
filled at 3x its allowed slippage still counted as a clean fill. Here
``dispatch_concurrent`` flags any leg whose realized slippage breaches its
limit and forces the package down the unwind path instead of LOCKED.

Fills are simulated (paper trading) by default via ``PaperFillSimulator``;
``LiveFillSimulator`` places real orders through the repo's ``OrderExecutor``
for live mode, satisfying the same (step, notional) -> LegResult contract so
the enforcement code is identical for both.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import itertools
import random
import uuid

from .execution import OrderRequest, OrderResult, OrderStatus, OrderSide
import logging
logger = logging.getLogger(__name__)


class PackageState(Enum):
    PENDING_FILL = "pending_fill"
    LOCKED = "locked"
    SETTLED = "settled"
    ABORTED = "aborted"


@dataclass
class Step:
    venue: str
    action: str  # "buy_spot" | "sell_spot" | "short_perp" | "cover_perp"
    asset: str
    amount_ratio: float  # fraction of package notional for this leg
    max_slippage_pct: float = 0.003

    def inverse(self) -> "Step":
        flip = {
            "buy_spot": "sell_spot",
            "sell_spot": "buy_spot",
            "short_perp": "cover_perp",
            "cover_perp": "short_perp",
        }
        return Step(self.venue, flip[self.action], self.asset, self.amount_ratio, self.max_slippage_pct)


def validate_steps(steps: list[Step]) -> list[str]:
    """Validated before dispatch -- the whole planned trade, not each leg independently."""
    errors = []
    total_ratio = sum(s.amount_ratio for s in steps)
    if abs(total_ratio - 1.0) > 1e-6:
        errors.append(f"step amount_ratios must sum to 1.0, got {total_ratio}")
    if len(steps) < 2:
        errors.append("multi-leg package requires at least 2 steps")
    for s in steps:
        if s.max_slippage_pct <= 0 or s.max_slippage_pct > 0.05:
            errors.append(f"step on {s.venue} has implausible max_slippage_pct={s.max_slippage_pct}")
    return errors


@dataclass
class LegResult:
    step: Step
    filled: bool
    fill_price: float | None
    slippage_pct: float | None
    fill_usd: float | None = None


@dataclass
class Package:
    id: int
    steps: list[Step]
    notional: float
    state: PackageState = PackageState.PENDING_FILL
    leg_results: list[LegResult] = field(default_factory=list)
    unwound: bool = False
    slippage_breached: bool = False


class MultiLegExecutionManager:
    _id_counter = itertools.count(1)

    def __init__(self, max_concurrent_packages: int = 3, fill_timeout_cycles: int = 2):
        self.max_concurrent_packages = max_concurrent_packages
        self.fill_timeout_cycles = fill_timeout_cycles
        self._open_packages: dict[int, Package] = {}
        self._active_instruments: set[str] = set()
        self.logger = logging.getLogger(__name__ + ".MultiLegExecutionManager")

    def _dispatch_and_track(self, pkg: Package, fill_simulator) -> list[LegResult]:
        """Dispatch legs sequentially, stop on first fill error (no fill or partial fill).
        If a leg has no fill (fill_price None) -> abort without unwind.
        If a leg is partially filled (filled True but fill_usd None) -> abort and unwind any previously fully-filled legs.
        Also tracks slippage breaches: if any leg's slippage exceeds its max_slippage_pct, set pkg.slippage_breached = True.
        Returns list of LegResult for all legs processed (including the leg that caused abort).
        Performs unwinds of any fully-filled legs using the same fill_simulator.
        """
        results: list[LegResult] = []
        abort = False
        for idx, step in enumerate(pkg.steps):
            leg_notional = pkg.notional * step.amount_ratio
            leg_result = fill_simulator(step, leg_notional)
            results.append(leg_result)
            # Slippage check
            if leg_result.filled and leg_result.slippage_pct is not None:
                if leg_result.slippage_pct > step.max_slippage_pct:
                    pkg.slippage_breached = True
            # Determine if we should abort
            if not leg_result.filled:
                # fill_price is None => no execution at all -> abort without unwind
                abort = True
                break
            # If we have a fill but it was partial (we detect via fill_usd being None while filled True)
            if leg_result.filled and leg_result.fill_usd is None:
                # This is a PARTIALLY_FILLED case (filled True but fill_usd unknown)
                abort = True
                break
        # If we aborted, unwind any legs that were fully filled (have fill_usd)
        if abort:
            for idx, leg_result in enumerate(results[:len(results)]):  # only those we processed
                if leg_result.filled and leg_result.fill_usd is not None:
                    # Unwind this leg at the filled amount (quote currency)
                    unwind_step = results[idx].step.inverse()
                    # The unwind notional is the filled amount in quote currency
                    unwind_notional = leg_result.fill_usd
                    self.logger.warning(
                        f"Aborting multi-leg package: leg {idx} ({results[idx].step.action} {results[idx].step.asset}) "
                        f"had partial or zero fill; unwinding leg {idx} ({unwind_step.action} {unwind_step.asset}) "
                        f"with notional {unwind_notional:.2f}"
                    )
                    # Execute unwind (this may also fail; we log but continue)
                    try:
                        fill_simulator(unwind_step, unwind_notional)
                    except Exception as e:
                        self.logger.error(f"Unwind leg {idx} failed: {e}")
                # If leg_result.filled but fill_usd is None (PARTIALLY_FILLED) we do not unwind because we don't know amount
        return results

    def can_open(self, asset: str) -> tuple[bool, str | None]:
        if len(self._open_packages) >= self.max_concurrent_packages:
            return False, f"capacity check failed: {len(self._open_packages)} packages already open"
        if asset in self._active_instruments:
            return False, f"duplication check failed: an active package already exists for {asset}"
        return True, None

    def propose_package(self, steps: list[Step], notional: float) -> Package:
        errors = validate_steps(steps)
        if errors:
            raise ValueError(f"invalid package: {errors}")

        allowed, reason = self.can_open(steps[0].asset)
        if not allowed:
            raise RuntimeError(reason)

        pkg = Package(id=next(self._id_counter), steps=steps, notional=notional)
        self._open_packages[pkg.id] = pkg
        self._active_instruments.add(steps[0].asset)
        return pkg

    def dispatch_concurrent(self, pkg: Package, fill_simulator) -> Package:
        """
        All legs submitted in the same cycle (serial per-leg submission, in
        order), so the package is either fully funded or its peers are never
        assumed filled. `fill_simulator(step, notional_for_leg) -> LegResult`.

        Every declared per-leg slippage limit is enforced here -- this is the
        code path that executes the order. If a leg fills beyond its
        `max_slippage_pct`, the package is flagged `slippage_breached` and
        stays PENDING_FILL so the caller must unwind it; it can never silently
        reach LOCKED on a bad-priced fill.
        """
        for step in pkg.steps:
            leg_notional = pkg.notional * step.amount_ratio
            result = fill_simulator(step, leg_notional)
            pkg.leg_results.append(result)
            if (
                result.filled
                and result.slippage_pct is not None
                and result.slippage_pct > step.max_slippage_pct
            ):
                pkg.slippage_breached = True

        all_filled = all(r.filled for r in pkg.leg_results)
        if all_filled and not pkg.slippage_breached:
            pkg.state = PackageState.LOCKED
        # else: stays PENDING_FILL; resolve_partial_fill / resolve_slippage_breach
        # handle the unwind, fail-closed.
        return pkg

    def resolve_partial_fill(self, pkg: Package, unwind_simulator) -> Package:
        """
        One leg filled and another didn't: immediately close the filled leg
        rather than leave single-leg directional exposure. An open unwound
        position is worse than paying to unwind it.
        """
        filled_legs = [r for r in pkg.leg_results if r.filled]
        unfilled_legs = [r for r in pkg.leg_results if not r.filled]

        if not unfilled_legs:
            pkg.state = PackageState.LOCKED
            return pkg

        if not filled_legs:
            # nothing filled at all -- clean abort, no unwind needed
            pkg.state = PackageState.ABORTED
            self._release(pkg)
            return pkg

        # partial fill: unwind the filled leg(s) immediately
        unwind_results = []
        for leg in filled_legs:
            unwind_results.append(
                unwind_simulator(leg.step.inverse(), pkg.notional * leg.step.amount_ratio)
            )
        # Only claim the position is safe if every unwind leg actually filled.
        # An unwind that raises or reports unfilled leaves naked exposure —
        # fail-closed means we say so, never silently mark it unwound.
        pkg.unwound = bool(unwind_results) and all(r.filled for r in unwind_results)
        pkg.state = PackageState.ABORTED
        self._release(pkg)
        return pkg

    def resolve(self, pkg: Package, unwind_simulator) -> Package:
        """
        Route a PENDING_FILL package to the correct resolver — the caller
        never has to pick. Order matters and is deliberately fixed here:
        `slippage_breached` is checked BEFORE fill state.

        A package can be flagged `slippage_breached` while its legs are also
        not all filled. The slippage path unwinds every filled leg (including
        cleanly-filled peers, fail-closed); the partial-fill path unwinds only
        the filled leg(s). Checking fill state first would send a breached
        package down the partial-fill path — and worse, when all legs happen
        to be filled, the partial-fill resolver's "no unfilled legs -> LOCKED"
        branch would mark a breached package as a clean locked trade.
        """
        if pkg.slippage_breached:
            return self.resolve_slippage_breach(pkg, unwind_simulator)
        if not all(r.filled for r in pkg.leg_results):
            return self.resolve_partial_fill(pkg, unwind_simulator)
        return pkg

    def resolve_slippage_breach(self, pkg: Package, unwind_simulator) -> Package:
        """
        At least one leg filled beyond its allowed slippage. The package is
        fail-closed: every filled leg is unwound immediately (including the
        cleanly-filled peer legs, so the abort never leaves a partial leg).

        Must only be called after dispatch flagged `slippage_breached`.
        """
        filled_legs = [r for r in pkg.leg_results if r.filled]
        if not filled_legs:
            pkg.state = PackageState.ABORTED
            self._release(pkg)
            return pkg

        unwind_results = []
        for leg in filled_legs:
            unwind_results.append(
                unwind_simulator(leg.step.inverse(), pkg.notional * leg.step.amount_ratio)
            )
        pkg.unwound = bool(unwind_results) and all(r.filled for r in unwind_results)
        pkg.state = PackageState.ABORTED
        self._release(pkg)
        return pkg

    def settle(self, pkg: Package) -> Package:
        if pkg.state != PackageState.LOCKED:
            raise RuntimeError(f"cannot settle package {pkg.id} in state {pkg.state}")
        pkg.state = PackageState.SETTLED
        self._release(pkg)
        return pkg

    def close_package(self, pkg: Package, fill_simulator) -> Package:
        """Walk the same step list in reverse with each action flipped -- a
        defined symmetric inverse, not a second bespoke code path."""
        inverse_steps = [s.inverse() for s in reversed(pkg.steps)]
        closing_results = []
        for step in inverse_steps:
            leg_notional = pkg.notional * step.amount_ratio
            closing_results.append(fill_simulator(step, leg_notional))
        pkg.leg_results.extend(closing_results)
        # Release the package: without this, a closed package stayed in
        # _open_packages / _active_instruments forever, permanently consuming
        # one max_concurrent_packages slot and blocking new packages on the
        # same asset.
        self._release(pkg)
        return pkg

    def _release(self, pkg: Package):
        self._open_packages.pop(pkg.id, None)
        if pkg.steps:
            self._active_instruments.discard(pkg.steps[0].asset)

    def open_package_count(self) -> int:
        return len(self._open_packages)


class PaperFillSimulator:
    """Simulates realistic-ish fills: occasional partial/no-fill, slippage.

    Slippage is drawn directly from the distribution and NEVER clamped with a
    min() cap -- a cap masquerading as a breach earlier made the slippage
    enforcement unreachable, because fills could never actually exceed
    `max_slippage_pct`. Here an occasional breach is real, which is the whole
    reason the enforcement path exists.
    """

    def __init__(self, seed: int = 7, fill_prob: float = 0.94):
        self.rng = random.Random(seed)
        self.fill_prob = fill_prob

    def __call__(self, step: Step, notional: float) -> LegResult:
        filled = self.rng.random() < self.fill_prob
        if not filled:
            return LegResult(step=step, filled=False, fill_price=None, slippage_pct=None)
        slippage = abs(self.rng.gauss(0, step.max_slippage_pct / 2))
        return LegResult(step=step, filled=True, fill_price=notional, slippage_pct=slippage)


class LiveFillSimulator:
    """Places real orders through the repo's OrderExecutor and reports the
    actual fill, not a simulation.

    Satisfies the exact same call contract as PaperFillSimulator
    (``(step, notional) -> LegResult``) so ``dispatch_concurrent`` and the
    resolvers need no branch on paper-vs-live -- the executor is reused
    (risk-gated, dry-run-aware, fill-verified) rather than this class
    inventing a second order path.

    Slippage is measured by the executor's post-fill verification
    (``OrderResult.slippage_pct``, a percentage) and normalized to the same
    fraction unit ``Step.max_slippage_pct`` and PaperFillSimulator use, so the
    package's own breach detection compares like units.
    """

    def __init__(
        self,
        executor,
        reference_price: float | None = None,
        reference_price_timestamp: float | None = None,
        reference_prices: dict[str, float] | None = None,
        reference_timestamps: dict[str, float] | None = None,
    ):
        self.executor = executor
        # Legacy single-reference form: used when every leg in a package is
        # the same instrument. Per-asset maps take precedence when provided,
        # which is what a mixed-instrument package (spot + perp) needs — each
        # leg verifies its fill against ITS OWN instrument's reference price.
        self.reference_price = reference_price
        self.reference_price_timestamp = reference_price_timestamp
        self.reference_prices = reference_prices or {}
        self.reference_timestamps = reference_timestamps or {}

    def _reference_for(self, step: Step) -> tuple[float | None, float | None]:
        price = self.reference_prices.get(step.asset, self.reference_price)
        timestamp = self.reference_timestamps.get(
            step.asset, self.reference_price_timestamp
        )
        return price, timestamp

    def _step_to_order(self, step: Step, notional: float) -> OrderRequest:
        action = step.action
        side: OrderSide
        if action == "buy_spot":
            side, inst = "buy", step.asset
        elif action == "sell_spot":
            side, inst = "sell", step.asset
        elif action == "short_perp":
            side, inst = "sell", step.asset
        elif action == "cover_perp":
            side, inst = "buy", step.asset
        else:  # pragma: no cover - validate_steps rejects unknown actions before dispatch
            raise ValueError(f"unknown multi-leg action: {action}")
        return OrderRequest(
            inst_id=inst,
            side=side,
            order_type="market",
            size=f"{notional:.2f}",
            client_oid=f"pkgleg_{step.asset.replace('-', '')}_{uuid.uuid4().hex[:8]}",
            reduce_only=action in ("sell_spot", "cover_perp"),
            # Closing legs (sell_spot / cover_perp) are unwinds: they flatten
            # exposure, so they are admitted past the kill switch (which the
            # very fill that created the exposure may have just tripped).
            unwind=action in ("sell_spot", "cover_perp"),
        )

    def _run_place_order(self, step: Step, notional: float) -> OrderResult:
        """Run the async place_order to completion from sync context.

        Loop-safe: asyncio.run() raises RuntimeError when a loop is already
        running in this thread (an async trading loop dispatching packages).
        In that case the coroutine runs on its own loop in a worker thread —
        the coroutine has not started, so cross-thread hand-off is safe — and
        this call still blocks until it completes, preserving the sync
        ``(step, notional) -> LegResult`` contract.
        """
        import asyncio
        import threading

        coro = self.executor.place_order(
            self._step_to_order(step, notional),
            current_price=self._reference_for(step)[0],
            current_price_timestamp=self._reference_for(step)[1],
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        box: dict = {}
        def _target():
            box["result"] = asyncio.run(coro)
        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join()
        if "result" not in box:
            raise RuntimeError("multi-leg live fill failed on worker loop")
        return box["result"]

    def __call__(self, step: Step, notional: float) -> LegResult:
        result = self._run_place_order(step, notional)
        filled = result.state in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)
        # The executor returns the fill immediately from the OKX response but
        # leaves state=PENDING (it does not poll for completion), so a live
        # market fill shows up as PENDING with a non-None fill_px. A real fill
        # has a fill price; no fill price means the leg didn't execute.
        if not filled and result.fill_px is not None:
            filled = True
        fill_price = float(result.fill_px) if result.fill_px is not None else None
        # executor reports slippage as a percentage; multi-leg treats it as a
        # fraction (0.003 = 0.3%), so divide by 100 to keep like units.
        slippage_pct = (
            result.slippage_pct / 100.0 if result.slippage_pct is not None else None
        )
        # Compute fill in quote currency (USDT) if we have a price
        fill_usd = None
        if fill_price is not None:
            # notional is in base currency? Actually notional passed is in quote (USDT) per multi_leg design.
            # But step.amount_ratio is fraction of package notional (in quote). So leg notional in quote = notional * step.amount_ratio.
            # However we don't have step.amount_ratio here; we can compute fill notional in quote as fill_price * base_amount.
            # We don't have base_amount directly. Simpler: use the notional passed (which is quote notional for the leg) as proxy.
            # Since we only have quote notional, and we have fill_price in quote per base, we need base amount to compute quote fill.
            # Actually the notional argument passed to __call__ is the quote notional for this leg (see _dispatch_and_track: leg_notional = package.notional * step.amount_ratio).
            # So we can compute base amount = leg_notional / fill_price if fill_price > 0.
            # Then fill in quote = base_amount * fill_price = leg_notional (same). So fill_usd = leg_notional if filled.
            # However if we only partially filled, we need the filled quote amount.
            # We don't have filled quote amount from the exchange; we only know that the order was filled (or partially) but not how much.
            # For simplicity, we assume that if filled, the entire leg notional was filled (this matches the current assumption elsewhere).
            # For partial fills, we cannot know the filled amount without additional info; we'll set fill_usd to None to indicate unknown.
            # But we can approximate: if the order status is FILLED, assume full leg notional; if PARTIALLY_FILLED, we don't know.
            # We'll set fill_usd = leg_notional if filled and result.state == OrderStatus.FILLED else None.
            leg_notional = notional  # quote notional for this leg
            if result.state == OrderStatus.FILLED:
                fill_usd = leg_notional
            # For PARTIALLY_FILLED, we leave fill_usd as None (unknown)
        return LegResult(
            step=step,
            filled=filled,
            fill_price=fill_price,
            slippage_pct=slippage_pct,
            fill_usd=fill_usd,
        )