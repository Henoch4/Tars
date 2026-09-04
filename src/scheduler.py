"""
Automated trading scheduler for TARS.

Runs trading cycles on a configurable interval using APScheduler.
Supports both async and sync execution, graceful shutdown, and health reporting.

Usage:
    python -m src.scheduler                    # run forever
    python -m src.scheduler --once             # run one cycle and exit
    python -m src.scheduler --interval 15      # custom interval (minutes)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, asdict
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .agent import AutonomousTradingAgent, TradingCycleResult
from .exchange import create_exchange_client
from .execution import RiskGate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


@dataclass
class SchedulerConfig:
    interval_minutes: int = 15
    assets: tuple[str, ...] = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "BNB-USDT-SWAP")
    dry_run: bool = True
    agent_id: str = "tars-scheduler"
    log_level: str = "INFO"


class LoopWatchdog:
    """External stall detector for the trading loop (S1).

    The loop publishes progress (heartbeat after each completed cycle);
    this object — running OUTSIDE the loop — compares it against the
    expected cadence. A path that stops the loop also stops any check
    placed inside it, so the check must live here, not in run_cycle.

    Fires on TRANSITIONS into/out of the stalled condition, never per
    tick. A None cursor means "never started" — distinct from stalled,
    and silent (no alert for a loop that hasn't run yet).
    """

    def __init__(self, expected_interval_s: float, grace_s: float = 300.0):
        self.bound_s = expected_interval_s * 2 + grace_s
        self._stalled = False

    @property
    def stalled(self) -> bool:
        return self._stalled

    def check(self, now: float, last_completed_at: float | None) -> str | None:
        """Return 'stalled' / 'recovered' on transition, else None."""
        if last_completed_at is None:
            return None
        stalled = (now - last_completed_at) > self.bound_s
        if stalled and not self._stalled:
            self._stalled = True
            return "stalled"
        if not stalled and self._stalled:
            self._stalled = False
            return "recovered"
        return None


class TradingScheduler:
    def __init__(self, config: SchedulerConfig):
        self.config = config
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.agent: Optional[AutonomousTradingAgent] = None
        self._shutdown = asyncio.Event()
        self._last_cycle: Optional[TradingCycleResult] = None
        self._cycle_count = 0
        self._error_count = 0
        self.watchdog = LoopWatchdog(config.interval_minutes * 60.0)
        self._heartbeat_path = os.getenv(
            "LOOP_HEARTBEAT_PATH", "data/loop_heartbeat.json")

    async def initialize(self) -> None:
        """Initialize the trading agent and exchange client."""
        logger.info("Initializing trading scheduler...")

        # Exchange client (OKX or Binance, based on EXCHANGE env var)
        exchange = os.getenv("EXCHANGE", "okx").lower()
        cli = create_exchange_client(exchange)
        logger.info(f"Using exchange: {exchange}")

        # Risk gate
        risk_gate = RiskGate()

        # Agent
        self.agent = AutonomousTradingAgent(
            okx_cli=cli,
            risk_gate=risk_gate,
            dry_run=self.config.dry_run,
            agent_id=self.config.agent_id,
        )

        logger.info(f"Scheduler initialized (dry_run={self.config.dry_run}, interval={self.config.interval_minutes}min)")

    def _write_heartbeat_file(self) -> None:
        """Persist the latest heartbeat for external watchers (and post-
        restart visibility). Best-effort: never fails the loop."""
        try:
            from .metrics import get_heartbeat
            import json
            import pathlib
            hb = get_heartbeat()
            if hb is None:
                return
            path = pathlib.Path(self._heartbeat_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(hb))
            tmp.replace(path)
        except Exception as e:  # noqa: BLE001 — heartbeat file is auxiliary
            logger.warning(f"Heartbeat file write failed: {e}")

    async def _watchdog_task(self) -> None:
        """Own task, outside the trading loop: sample the published cursor
        and fire on stalled/recovered transitions (S1)."""
        from .metrics import get_heartbeat, set_gauge
        from .alerting import send_alert
        while not self._shutdown.is_set():
            try:
                hb = get_heartbeat()
                last = hb["completed_at"] if hb else None
                transition = self.watchdog.check(time.time(), last)
                set_gauge("tars_loop_stopped_contributing_condition_active",
                          1.0 if self.watchdog.stalled else 0.0)
                if transition == "stalled":
                    send_alert(
                        "LOOP_STALLED", "critical",
                        f"No completed cycle for >{self.watchdog.bound_s:.0f}s "
                        f"(last: {hb['cycle_id'] if hb else 'never'}).",
                    )
                elif transition == "recovered":
                    send_alert(
                        "LOOP_RECOVERED", "warning",
                        f"Cycle loop contributing again (last: {hb['cycle_id']}).",
                    )
            except Exception as e:  # noqa: BLE001 — watchdog never kills the loop
                logger.warning(f"Watchdog check failed: {e}")
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                pass

    async def run_cycle(self) -> TradingCycleResult:
        """Execute a single trading cycle."""
        if not self.agent:
            raise RuntimeError("Agent not initialized. Call initialize() first.")

        from .metrics import (
            OUTCOME_ERROR,
            beat as _metrics_beat,
            inc as _metrics_inc,
        )
        self._cycle_count += 1
        cycle_num = self._cycle_count
        logger.info(f"Starting cycle #{cycle_num} for assets: {self.config.assets}")
        _metrics_inc("tars_cycles_total", {})

        try:
            result = await self.agent.run_trading_cycle(list(self.config.assets))
            self._last_cycle = result
            self._write_heartbeat_file()

            # Log summary
            signals = len(result.signals)
            decisions = len(result.decisions)
            executions = len(result.executions)
            errors = len(result.errors)

            logger.info(
                f"Cycle #{cycle_num} complete: {signals} signals, {decisions} decisions, "
                f"{executions} executions, {errors} errors"
            )

            if errors:
                for err in result.errors:
                    logger.warning(f"  Error: {err}")

            return result

        except Exception as e:
            self._error_count += 1
            _metrics_inc("tars_cycle_errors_total", {})
            # The agent never beat (it raised): record the error outcome here
            # so "loop ran and failed" is distinguishable from silence.
            _metrics_beat(f"cycle_{cycle_num}_failed", OUTCOME_ERROR,
                          counts={}, errors=1)
            self._write_heartbeat_file()
            logger.error(f"Cycle #{cycle_num} failed: {e}", exc_info=True)
            raise

    async def start(self, run_once: bool = False) -> None:
        """Start the scheduler."""
        await self.initialize()

        if run_once:
            logger.info("Running single cycle...")
            await self.run_cycle()
            return

        # Schedule recurring cycles
        trigger = IntervalTrigger(minutes=self.config.interval_minutes)
        self.scheduler.add_job(
            self.run_cycle,
            trigger,
            id="trading_cycle",
            max_instances=1,  # prevent overlapping cycles
            coalesce=True,    # if missed, run once not catch-up
            misfire_grace_time=300,  # 5 min grace
        )

        self.scheduler.start()
        logger.info(f"Scheduler started. Running every {self.config.interval_minutes} minutes.")

        # S1: the stall check runs as its own task, outside the trading loop.
        watchdog = asyncio.create_task(self._watchdog_task())

        # Wait for shutdown signal
        await self._shutdown.wait()
        watchdog.cancel()

        logger.info("Shutdown signal received, stopping scheduler...")
        self.scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped.")

    def shutdown(self) -> None:
        """Trigger graceful shutdown."""
        self._shutdown.set()

    def get_status(self) -> dict:
        """Get scheduler health status."""
        from .metrics import get_heartbeat
        return {
            "running": self.scheduler.running if hasattr(self.scheduler, "running") else False,
            "cycle_count": self._cycle_count,
            "error_count": self._error_count,
            "last_cycle": asdict(self._last_cycle) if self._last_cycle else None,
            "heartbeat": get_heartbeat(),
            "watchdog_stalled": self.watchdog.stalled,
            "next_run": str(self.scheduler.get_job("trading_cycle").next_run_time)
            if self.scheduler.get_job("trading_cycle") else None,
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TARS Automated Trading Scheduler")
    parser.add_argument(
        "--interval", "-i", type=int, default=15,
        help="Interval between cycles in minutes (default: 15)"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one cycle and exit"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=None,
        help="Force dry-run mode (default: from DRY_RUN env)"
    )
    parser.add_argument(
        "--assets", "-a", nargs="+",
        default=["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "BNB-USDT-SWAP"],
        help="Assets to trade"
    )
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO", help="Log level"
    )
    return parser.parse_args()


async def _async_main() -> int:
    args = _parse_args()

    logging.getLogger().setLevel(args.log_level)

    # Determine dry_run from env or flag
    dry_run_env = os.getenv("DRY_RUN", "true").lower() == "true"
    dry_run = args.dry_run if args.dry_run is not None else dry_run_env

    config = SchedulerConfig(
        interval_minutes=args.interval,
        assets=tuple(args.assets),
        dry_run=dry_run,
        agent_id=os.getenv("AGENT_ID", "tars-scheduler"),
    )

    scheduler = TradingScheduler(config)

    # Handle signals for graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, scheduler.shutdown)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await scheduler.start(run_once=args.once)
        return 0
    except KeyboardInterrupt:
        logger.info("Interrupted")
        return 0
    except Exception as e:
        logger.error(f"Scheduler failed: {e}", exc_info=True)
        return 1


def main() -> int:
    """Entry point for console script."""
    return asyncio.run(_async_main())


if __name__ == "__main__":
    sys.exit(main())