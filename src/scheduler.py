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


class TradingScheduler:
    def __init__(self, config: SchedulerConfig):
        self.config = config
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.agent: Optional[AutonomousTradingAgent] = None
        self._shutdown = asyncio.Event()
        self._last_cycle: Optional[TradingCycleResult] = None
        self._cycle_count = 0
        self._error_count = 0

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

    async def run_cycle(self) -> TradingCycleResult:
        """Execute a single trading cycle."""
        if not self.agent:
            raise RuntimeError("Agent not initialized. Call initialize() first.")

        self._cycle_count += 1
        cycle_num = self._cycle_count
        logger.info(f"Starting cycle #{cycle_num} for assets: {self.config.assets}")

        try:
            result = await self.agent.run_trading_cycle(list(self.config.assets))
            self._last_cycle = result

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

        # Wait for shutdown signal
        await self._shutdown.wait()

        logger.info("Shutdown signal received, stopping scheduler...")
        self.scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped.")

    def shutdown(self) -> None:
        """Trigger graceful shutdown."""
        self._shutdown.set()

    def get_status(self) -> dict:
        """Get scheduler health status."""
        return {
            "running": self.scheduler.running if hasattr(self.scheduler, "running") else False,
            "cycle_count": self._cycle_count,
            "error_count": self._error_count,
            "last_cycle": asdict(self._last_cycle) if self._last_cycle else None,
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