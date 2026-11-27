"""Production-shaped GTA runner using TARS policy, book, and executor layers.

The runner is an orchestration shell only: discovery, enrichment, execution,
and exit handling are injected. This keeps the state machine testable and
prevents a partial deployment from silently inventing a live trading path.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable

from .gta_book import GtaBook
from .gta_event import GtaEventPolicy, evaluate_candidate
from .gta_reconciliation import ReconciliationResult

logger = logging.getLogger(__name__)


class RunnerState(str, Enum):
    DISABLED = "disabled"
    RECONCILING = "reconciling"
    ARMED = "armed"
    RUNNING = "running"
    EMERGENCY_SHUTDOWN = "emergency_shutdown"


@dataclass(frozen=True)
class RunnerStatus:
    state: RunnerState
    reason: str
    open_positions: int
    spent_usd: float


DiscoveryFn = Callable[[], list[dict[str, Any]] | Awaitable[list[dict[str, Any]]]]
ExitFn = Callable[[str, Any], Any | Awaitable[Any]]
ReconcileFn = Callable[[], ReconciliationResult | Awaitable[ReconciliationResult]]


class GtaLiveRunner:
    """Window-gated runner; no automatic broadcast outside explicit arming."""

    def __init__(
        self,
        policy: GtaEventPolicy,
        book: GtaBook,
        *,
        discovery: DiscoveryFn,
        executor: Any | None = None,
        exit_handler: ExitFn | None = None,
        reconcile: ReconcileFn | None = None,
        flatten_handler: Callable[[str], Any | Awaitable[Any]] | None = None,
        reconcile_every_ticks: int = 1,
        poll_interval_s: float = 300.0,
    ):
        self.policy = policy
        self.book = book
        self.discovery = discovery
        self.executor = executor
        self.exit_handler = exit_handler
        self.reconcile = reconcile
        self.flatten_handler = flatten_handler
        self.reconcile_every_ticks = max(1, int(reconcile_every_ticks))
        self.poll_interval_s = float(poll_interval_s)
        self.state = RunnerState.DISABLED
        self.reason = "not started"
        self._stop = asyncio.Event()
        self._emergency = False
        self._ticks = 0
        self._reconciled_ok = reconcile is None

    def status(self) -> RunnerStatus:
        return RunnerStatus(
            state=self.state,
            reason=self.reason,
            open_positions=len(self.book.positions),
            spent_usd=self.book.risk.spent_usd,
        )

    def _sync_arm(self, now: datetime | None = None) -> None:
        if not self._reconciled_ok:
            self.state = RunnerState.DISABLED
            self.reason = "reconciliation required"
            return
        armed, reason = self.policy.broadcast_allowed(now)
        if self._emergency:
            self.state = RunnerState.EMERGENCY_SHUTDOWN
            self.reason = "emergency shutdown engaged"
        elif armed:
            self.state = RunnerState.ARMED
            self.reason = reason
        else:
            self.state = RunnerState.DISABLED
            self.reason = reason

    async def startup_reconciliation(self) -> RunnerStatus:
        """Load durable local state; unknown on-chain state remains a block."""
        self.state = RunnerState.RECONCILING
        self.reason = "loading durable GTA state"
        # GtaBook loads its state during construction. A real on-chain
        # reconciler must be injected before enabling live positions; never
        # pretend an unavailable wallet read means zero exposure.
        if self.reconcile is not None:
            result = self.reconcile()
            if asyncio.iscoroutine(result):
                result = await result
            self._reconciled_ok = bool(result.ok)
            if not self._reconciled_ok:
                self.state = RunnerState.DISABLED
                self.reason = f"reconciliation blocked: {result.reason}"
                return self.status()
        self._sync_arm()
        return self.status()

    async def _call_discovery(self) -> list[dict[str, Any]]:
        result = self.discovery()
        if asyncio.iscoroutine(result):
            result = await result
        return result if isinstance(result, list) else []

    async def _call_exit(self, mint: str, decision: Any) -> Any:
        if self.exit_handler is None:
            return None
        result = self.exit_handler(mint, decision)
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def run_once(self, now: datetime | None = None) -> RunnerStatus:
        """Run one bounded cycle; exceptions leave the runner disarmed."""
        if self._emergency:
            self._sync_arm(now)
            return self.status()
        self._ticks += 1
        if self.reconcile is not None and self._ticks % self.reconcile_every_ticks == 0:
            result = self.reconcile()
            if asyncio.iscoroutine(result):
                result = await result
            self._reconciled_ok = bool(result.ok)
            if not self._reconciled_ok:
                self.state = RunnerState.DISABLED
                self.reason = f"reconciliation blocked: {result.reason}"
                return self.status()
        self._sync_arm(now)
        if self.state != RunnerState.ARMED:
            return self.status()
        self.state = RunnerState.RUNNING
        self.reason = "processing candidate and exit cycle"
        try:
            candidates = await self._call_discovery()
            prices_by_mint = {
                c.get("mint"): c.get("price_usd")
                for c in candidates
                if isinstance(c, dict)
            }
            for candidate in candidates:
                decision = evaluate_candidate(self.policy, candidate, now=now)
                if not decision.allowed or self.executor is None:
                    continue
                sol_price = candidate.get("sol_price_usd")
                if sol_price is None:
                    logger.info("GTA entry refused: SOL price unavailable")
                    continue
                result = self.executor.enter(
                    candidate,
                    sol_price_usd=sol_price,
                    confirm=candidate.get("confirm", ""),
                    now_ts=now.timestamp() if now else None,
                )
                if not result.get("broadcast"):
                    logger.info("GTA entry refused: %s", result.get("reason"))
            for mint in list(self.book.positions):
                price = prices_by_mint.get(mint)
                if price is None:
                    continue
                decision = self.book.exit_decision(mint, float(price), now=now)
                if decision.action not in ("hold", "halt"):
                    await self._call_exit(mint, decision)
            self._sync_arm(now)
        except Exception as exc:  # fail closed for the whole cycle
            logger.exception("GTA runner cycle failed")
            self.state = RunnerState.DISABLED
            self.reason = f"cycle failure: {type(exc).__name__}"
        return self.status()

    async def emergency_shutdown(self, reason: str = "manual") -> RunnerStatus:
        self._emergency = True
        self.state = RunnerState.EMERGENCY_SHUTDOWN
        self.reason = reason
        if self.flatten_handler is not None:
            try:
                result = self.flatten_handler(reason)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("GTA emergency flatten failed")
        self._stop.set()
        logger.critical("GTA emergency shutdown: %s", reason)
        return self.status()

    async def run_loop(self) -> None:
        await self.startup_reconciliation()
        while not self._stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_s)
            except asyncio.TimeoutError:
                pass


def install_signal_handlers(runner: GtaLiveRunner) -> None:
    """Install best-effort SIGINT/SIGTERM emergency handlers."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(
                runner.emergency_shutdown(f"signal {s.name}")))
        except (NotImplementedError, RuntimeError):
            # Windows event loops may not support signal handlers; callers
            # still retain the explicit emergency_shutdown API.
            continue
