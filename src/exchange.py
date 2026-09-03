"""
Exchange client factory — unified entry point for OKX and Binance.

Usage:
    from .exchange import create_exchange_client
    client = create_exchange_client()  # reads EXCHANGE env var
    # client is either OkxCli or BinanceClient, both implement the same interface
"""
from __future__ import annotations

import logging
import os
import pathlib

logger = logging.getLogger(__name__)


def _load_dotenv() -> None:
    """Minimal .env loader (no new dependency).

    Python does NOT auto-load .env on Windows — os.getenv alone would miss
    everything in the repo's .env file. This parses KEY=VALUE lines from the
    repo-root .env and sets only keys that aren't already in the environment
    (real env vars always win). Never logs values.
    """
    try:
        root = pathlib.Path(__file__).resolve().parent.parent
        env_path = root / ".env"
        if not env_path.is_file():
            return
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as e:  # a broken .env must never crash the app
        logger.warning(f".env loader skipped: {e}")


_load_dotenv()


def create_exchange_client(exchange: str | None = None):
    """Create an exchange client based on the EXCHANGE env var.

    Returns either OkxCli or BinanceClient — both implement the same async
    interface (run, check_auth, balance_all, positions, smartmoney_signal).

    EXCHANGE env var: "okx" (default) or "binance".
    """
    # Chained `or` with a final literal keeps this str under every mypy
    # version (single-arg getenv is str | None; the literal closes it).
    name = exchange or os.getenv("EXCHANGE") or "okx"
    exchange = name.lower().strip()

    if exchange == "binance":
        from .binance_client import BinanceClient, BinanceConfig

        config = BinanceConfig(
            api_key=os.getenv("BINANCE_API_KEY", ""),
            api_secret=os.getenv("BINANCE_API_SECRET", ""),
            testnet=os.getenv("BINANCE_TESTNET", "false").lower() == "true",
            base_url=os.getenv("BINANCE_BASE_URL"),
        )
        # Never log key material — presence only.
        logger.info(
            f"Using Binance client (testnet={config.testnet}, "
            f"key={'SET' if config.api_key else 'MISSING'})"
        )
        return BinanceClient(config)

    # Default: OKX
    from .okx_cli import OkxCli, OkxCliConfig

    # Distinct name from the BinanceConfig above — reusing `config` makes
    # mypy pin the variable to BinanceConfig and reject this branch.
    okx_config = OkxCliConfig(
        demo=os.getenv("OKX_DEMO", "true").lower() == "true",
        profile=os.getenv("OKX_PROFILE"),
    )
    logger.info(f"Using OKX client (demo={okx_config.demo})")
    return OkxCli(okx_config)
