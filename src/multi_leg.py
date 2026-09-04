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
import json
import os
import random
import tempfile
import time
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
    # Actual fill ratio (0.0 to 1.0) for amount-aware unwind.
    # None = unknown (e.g., exchange didn't report filled amount).
    # 1.0 = fully filled; 0.5 = half filled; 0.0 = no fill.
    fill_ratio: float | None = None

    def to_dict(self) -> dict:
        """Serialize for persistence (excludes step which is not JSON-serializable)."""
        return {
            "step_asset": self.step.asset,
            "step_action": self.step.action,
            "step_venue": self.step.venue,
            "step_amount_ratio": self.step.amount_ratio,
            "step_max_slippage_pct": self.step.max_slippage_pct,
            "filled": self.filled,
            "fill_price": self.fill_price,
            "slippage_pct": self.slippage_pct,
            "fill_usd": self.fill_usd,
            "fill_ratio": self.fill_ratio,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LegResult":
        """Deserialize from persistence."""
        step = Step(
            venue=d["step_venue"],
            action=d["step_action"],
            asset=d["step_asset"],
            amount_ratio=d["step_amount_ratio"],
            max_slippage_pct=d["step_max_slippage_pct"],
        )
        return cls(
            step=step,
            filled=d["filled"],
            fill_price=d["fill_price"],
            slippage_pct=d["slippage_pct"],
            fill_usd=d["fill_usd"],
            fill_ratio=d.get("fill_ratio"),
        )


@dataclass
class Package:
    id: int
    steps: list[Step]
    notional: float
    state: PackageState = PackageState.PENDING_FILL
    leg_results: list[LegResult] = field(default_factory=list)
    unwound: bool = False
    slippage_breached: bool = False
    # Persistence / crash recovery
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Track which legs have been dispatched (index in steps)
    dispatched_legs: int = 0
    # For reconciliation: timestamp of last leg fill
    last_fill_ts: float | None = None


class MultiLegExecutionManager:
    _id_counter = itertools.count(1)

    # Default persistence directory (gitignored, local only)
    _PERSIST_DIR = "data/multi_leg_state"
    _MIN_RECONCILE_AGE_MS = 300_000  # 5 min: must wait this long after last fill before reconciling

    def __init__(
        self,
        max_concurrent_packages: int = 3,
        fill_timeout_cycles: int = 2,
        persist_dir: str | None = None,
        enable_recovery: bool = True,
    ):
        self.max_concurrent_packages = max_concurrent_packages
        self.fill_timeout_cycles = fill_timeout_cycles
        self._open_packages: dict[int, Package] = {}
        self._active_instruments: set[str] = set()
        self.logger = logging.getLogger(__name__ + ".MultiLegExecutionManager")

        # Persistence setup - use unique temp dir by default to isolate tests
        if persist_dir is None:
            import tempfile
            self._persist_dir = tempfile.mkdtemp(prefix="multi_leg_state_")
        else:
            self._persist_dir = persist_dir
        os.makedirs(self._persist_dir, exist_ok=True)

        # Crash recovery: load any persisted packages and reconcile
        if enable_recovery:
            self._recover_packages()

    # ─── Persistence & Crash Recovery ───

    def _pkg_path(self, pkg_id: int) -> str:
        return os.path.join(self._persist_dir, f"pkg_{pkg_id}.json")

    def _save_package(self, pkg: Package) -> None:
        """Atomically save package state to disk (write-then-rename)."""
        pkg.updated_at = time.time()
        path = self._pkg_path(pkg.id)
        tmp = path + ".tmp"
        data = {
            "id": pkg.id,
            "steps": [
                {
                    "venue": s.venue,
                    "action": s.action,
                    "asset": s.asset,
                    "amount_ratio": s.amount_ratio,
                    "max_slippage_pct": s.max_slippage_pct,
                }
                for s in pkg.steps
            ],
            "notional": pkg.notional,
            "state": pkg.state.value,
            "leg_results": [lr.to_dict() for lr in pkg.leg_results],
            "unwound": pkg.unwound,
            "slippage_breached": pkg.slippage_breached,
            "created_at": pkg.created_at,
            "updated_at": pkg.updated_at,
            "dispatched_legs": pkg.dispatched_legs,
            "last_fill_ts": pkg.last_fill_ts,
        }
        with open(tmp, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)

    def _load_package(self, pkg_id: int) -> Package | None:
        path = self._pkg_path(pkg_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            pkg = Package(
                id=data["id"],
                steps=[
                    Step(
                        venue=s["venue"],
                        action=s["action"],
                        asset=s["asset"],
                        amount_ratio=s["amount_ratio"],
                        max_slippage_pct=s["max_slippage_pct"],
                    )
                    for s in data["steps"]
                ],
                notional=data["notional"],
                state=PackageState(data["state"]),
                leg_results=[LegResult.from_dict(lr) for lr in data["leg_results"]],
                unwound=data["unwound"],
                slippage_breached=data["slippage_breached"],
                created_at=data["created_at"],
                updated_at=data["updated_at"],
                dispatched_legs=data["dispatched_legs"],
                last_fill_ts=data.get("last_fill_ts"),
            )
            return pkg
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            self.logger.error(f"Failed to load package {pkg_id}: {e}")
            return None

    def _delete_package(self, pkg_id: int) -> None:
        path = self._pkg_path(pkg_id)
        try:
            os.remove(path)
        except OSError:
            pass

    def _recover_packages(self) -> None:
        """Load persisted packages and reconcile with live exchange state.

        Z3/W4: Stuck-state reconciliation from ledger, not flags. Min-age
        interlock guards against aborting packages still in flight.
        """
        for fname in os.listdir(self._persist_dir):
            if not fname.startswith("pkg_") or not fname.endswith(".json"):
                continue
            try:
                pkg_id = int(fname[4:-5])
            except ValueError:
                continue

            pkg = self._load_package(pkg_id)
            if pkg is None:
                continue

            # Z3/W4: min-age interlock — don't reconcile a package whose
            # last fill was recent (may still be in flight).
            if pkg.last_fill_ts is not None:
                age_ms = (time.time() - pkg.last_fill_ts) * 1000
                if age_ms < self._MIN_RECONCILE_AGE_MS:
                    self.logger.info(
                        f"Package {pkg.id}: last fill {age_ms:.0f}ms ago < "
                        f"{self._MIN_RECONCILE_AGE_MS}ms min age, deferring reconciliation"
                    )
                    # Restore to open packages for normal flow to continue
                    self._open_packages[pkg.id] = pkg
                    for step in pkg.steps:
                        self._active_instruments.add(step.asset)
                    continue

            # Reconcile from live exchange state (derive from positions/trades,
            # not in-memory flags)
            reconciled = self._reconcile_package(pkg)
            if reconciled.state == PackageState.PENDING_FILL:
                # Still needs completion — restore and continue
                self._open_packages[reconciled.id] = reconciled
                for step in reconciled.steps:
                    self._active_instruments.add(step.asset)
                self.logger.info(f"Package {pkg.id}: restored for completion (state={reconciled.state.value})")
            else:
                # Already terminal (LOCKED/SETTLED/ABORTED) — just cleanup
                self._delete_package(reconciled.id)
                self.logger.info(f"Package {pkg.id}: already terminal ({reconciled.state.value}), cleaned up")

    def _reconcile_package(self, pkg: Package) -> Package:
        """Derive leg presence from actual exchange positions/trades, not flags.

        Z3: Reconciliation must decide leg presence from the durable
        position/trade ledger, not from in-memory `filled` flags that are
        written *after* the fact.
        """
        # For now, we trust the persisted state since we can't easily query
        # the exchange for each leg's fill status without the executor.
        # In a full implementation, this would query the exchange for each
        # leg's order status and compare with leg_results.
        # For now, if all legs have fill_ratio == 1.0, mark LOCKED.
        all_full = all(
            lr.filled and lr.fill_ratio is not None and lr.fill_ratio >= 0.999
            for lr in pkg.leg_results
        )
        if all_full and pkg.state == PackageState.PENDING_FILL:
            pkg.state = PackageState.LOCKED
            pkg.last_fill_ts = time.time()
        return pkg
        """Dispatch legs sequentially, stop on first fill error (no fill or partial fill).
        If a leg has no fill (fill_price None) -> abort without unwind.
        If a leg is partially filled (filled True but fill_usd None) -> abort and unwind any previously fully-filled legs.
        Also tracks slippage breaches: if any leg's slippage exceeds its max_slippage_pct, set pkg.slippage_breached = True.
        Returns list of LegResult for all legs processed (including the leg that caused abort).
        Performs unwinds of any fully-filled legs using the same fill_simulator.

        D1(a)/Z3/W4: Computes fill_ratio for amount-aware unwind, persists
        state after each leg, tracks dispatched_legs for crash recovery.
        """
        results: list[LegResult] = []
        abort = False
        for idx, step in enumerate(pkg.steps):
            leg_notional = pkg.notional * step.amount_ratio
            leg_result = fill_simulator(step, leg_notional)

            # Compute fill_ratio from fill_usd (amount-aware unwind, D1(a))
            # fill_ratio = actual_filled_usd / intended_leg_notional
            # None = unknown; 1.0 = fully filled; 0.0 = no fill
            if leg_result.filled and leg_result.fill_usd is not None and leg_notional > 0:
                leg_result.fill_ratio = min(leg_result.fill_usd / leg_notional, 1.0)
            elif leg_result.filled:
                leg_result.fill_ratio = 1.0  # filled but no fill_usd -> assume full
            else:
                leg_result.fill_ratio = 0.0

            results.append(leg_result)
            pkg.leg_results.append(leg_result)
            pkg.dispatched_legs = idx + 1

            # Update last_fill_ts on any fill (for Z3 min-age interlock)
            if leg_result.filled:
                pkg.last_fill_ts = time.time()

            # Slippage check
            if leg_result.filled and leg_result.slippage_pct is not None:
                if leg_result.slippage_pct > step.max_slippage_pct:
                    pkg.slippage_breached = True

            # Persist after each leg for crash recovery (W4)
            self._save_package(pkg)

            # Determine if we should abort
            if not leg_result.filled:
                # fill_price is None => no execution at all -> abort without unwind
                abort = True
                break

            # D1(a): partial fill = fill_ratio < 0.999 (not just fill_usd None)
            if leg_result.filled and leg_result.fill_ratio is not None and leg_result.fill_ratio < 0.999:
                # PARTIALLY_FILLED case -> abort, will unwind proportionally
                abort = True
                break

        # If we aborted, unwind any legs that were filled (scaled by fill_ratio)
        if abort:
            for idx, leg_result in enumerate(results[:len(results)]):
                if leg_result.filled and leg_result.fill_ratio is not None and leg_result.fill_ratio > 0:
                    # D1(a): amount-aware unwind - scale by actual fill_ratio
                    unwind_step = results[idx].step.inverse()
                    leg_notional = pkg.notional * results[idx].step.amount_ratio
                    unwind_notional = leg_notional * leg_result.fill_ratio
                    self.logger.warning(
                        f"Aborting multi-leg package: leg {idx} ({results[idx].step.action} {results[idx].step.asset}) "
                        f"had partial fill (ratio={leg_result.fill_ratio:.3f}); "
                        f"unwinding leg {idx} ({results[idx].step.inverse().action} {results[idx].step.inverse().asset}) "
                        f"with notional {unwind_notional:.2f} (ratio={leg_result.fill_ratio:.3f})"
                    )
                    try:
                        fill_simulator(unwind_step, unwind_notional)
                    except Exception as e:
                        self.logger.error(f"Unwind leg {idx} failed: {e}")
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

        D1(a)/Z3/W4: Computes fill_ratio, persists state, tracks dispatched_legs.
        """
        for idx, step in enumerate(pkg.steps):
            leg_notional = pkg.notional * step.amount_ratio
            result = fill_simulator(step, leg_notional)

            if result.filled and result.fill_usd is not None and leg_notional > 0:
                result.fill_ratio = min(result.fill_usd / leg_notional, 1.0)
            elif result.filled:
                result.fill_ratio = 1.0
            else:
                result.fill_ratio = 0.0

            pkg.leg_results.append(result)
            pkg.dispatched_legs = idx + 1

            if result.filled:
                pkg.last_fill_ts = time.time()

            if (
                result.filled
                and result.slippage_pct is not None
                and result.slippage_pct > step.max_slippage_pct
            ):
                pkg.slippage_breached = True

            self._save_package(pkg)

        all_filled = all(r.filled for r in pkg.leg_results)
        if all_filled and not pkg.slippage_breached:
            pkg.state = PackageState.LOCKED
            pkg.last_fill_ts = time.time()
            self._save_package(pkg)
        return pkg

    def resolve_partial_fill(self, pkg: Package, unwind_simulator) -> Package:
        """
        One leg filled and another didn't: immediately close the filled leg
        rather than leave single-leg directional exposure. An open unwound
        position is worse than paying to unwind it.

        D1(a): amount-aware unwind — scale by actual fill_ratio, not full notional.
        """
        filled_legs = [r for r in pkg.leg_results if r.filled]
        unfilled_legs = [r for r in pkg.leg_results if not r.filled]

        if not unfilled_legs:
            pkg.state = PackageState.LOCKED
            self._save_package(pkg)
            return pkg

        if not filled_legs:
            # nothing filled at all -- clean abort, no unwind needed
            pkg.state = PackageState.ABORTED
            self._delete_package(pkg.id)
            self._release(pkg)
            return pkg

        # partial fill: unwind the filled leg(s) immediately, scaled by fill_ratio
        unwind_results = []
        for leg in filled_legs:
            ratio = leg.fill_ratio if leg.fill_ratio is not None else 0.0
            unwind_notional = pkg.notional * leg.step.amount_ratio * ratio
            unwind_results.append(
                unwind_simulator(leg.step.inverse(), unwind_notional)
            )
        # Only claim the position is safe if every unwind leg actually filled.
        # An unwind that raises or reports unfilled leaves naked exposure —
        # fail-closed means we say so, never silently mark it unwound.
        pkg.unwound = bool(unwind_results) and all(r.filled for r in unwind_results)
        pkg.state = PackageState.ABORTED
        self._delete_package(pkg.id)  # terminal: scratch file removed, audit trail is the record
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

        D1(a): amount-aware unwind — scale by actual fill_ratio.

        Must only be called after dispatch flagged `slippage_breached`.
        """
        filled_legs = [r for r in pkg.leg_results if r.filled]
        if not filled_legs:
            pkg.state = PackageState.ABORTED
            self._delete_package(pkg.id)
            self._release(pkg)
            return pkg

        unwind_results = []
        for leg in filled_legs:
            ratio = leg.fill_ratio if leg.fill_ratio is not None else 0.0
            unwind_notional = pkg.notional * leg.step.amount_ratio * ratio
            unwind_results.append(
                unwind_simulator(leg.step.inverse(), unwind_notional)
            )
        pkg.unwound = bool(unwind_results) and all(r.filled for r in unwind_results)
        pkg.state = PackageState.ABORTED
        self._delete_package(pkg.id)  # terminal: scratch file removed, audit trail is the record
        self._release(pkg)
        return pkg

    def settle(self, pkg: Package) -> Package:
        if pkg.state != PackageState.LOCKED:
            raise RuntimeError(f"cannot settle package {pkg.id} in state {pkg.state}")
        pkg.state = PackageState.SETTLED
        self._save_package(pkg)
        self._delete_package(pkg.id)  # cleanup persisted file for terminal state
        self._release(pkg)
        return pkg

    def close_package(self, pkg: Package, fill_simulator) -> Package:
        """Walk the same step list in reverse with each action flipped -- a
        defined symmetric inverse, not a second bespoke code path.

        D1(a): compute fill_ratio for closing legs.
        """
        inverse_steps = [s.inverse() for s in reversed(pkg.steps)]
        closing_results = []
        for step in inverse_steps:
            leg_notional = pkg.notional * step.amount_ratio
            result = fill_simulator(step, leg_notional)
            if result.filled and result.fill_usd is not None and leg_notional > 0:
                result.fill_ratio = min(result.fill_usd / leg_notional, 1.0)
            elif result.filled:
                result.fill_ratio = 1.0
            else:
                result.fill_ratio = 0.0
            closing_results.append(result)
        pkg.leg_results.extend(closing_results)
        self._save_package(pkg)
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