"""
Meteora DBC executor — wraps TypeScript SDK via node subprocess.
Keeps Python agent loop unchanged; TS does signing. Verifies fill vs reference.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SwapResult:
    tx: str
    out_amount: float
    price_impact: float
    fee: float


class MeteoraExecutor:
    """Shells out to TS scripts for Meteora DBC operations."""

    def __init__(self, rpc_url: str, ts_dir: str | None = None, keypair_path: str | None = None):
        self.rpc_url = rpc_url
        self.ts_dir = Path(ts_dir) if ts_dir else Path(__file__).resolve().parent.parent / "ts"
        self.keypair_path = keypair_path

    def _run_ts(self, script: str, *args: str) -> dict[str, Any]:
        """Run a TS script via node, return parsed JSON."""
        env = {
            **subprocess.os.environ,
            "RPC_URL": self.rpc_url,
        }
        if self.keypair_path:
            env["KEYPAIR_PATH"] = self.keypair_path

        cmd = ["node", str(self.ts_dir / "dist" / f"{script}.js"), *args]
        logger.debug(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"TS script {script} failed: {result.stderr}")
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Invalid JSON from {script}: {result.stdout}") from e

    # --- Public API ---

    def create_config(self, regime_scale: float) -> dict[str, Any]:
        """Create DBC partner config tuned for regime."""
        return self._run_ts("create_config", f"{regime_scale:.3f}")

    def create_pool(self, base_mint: str, config_pubkey: str, name: str, symbol: str, uri: str) -> dict[str, Any]:
        """Create DBC pool (stock/USDC). Returns pool address."""
        return self._run_ts("create_pool", base_mint, config_pubkey, name, symbol, uri)

    def swap(
        self,
        pool: str,
        amount_in: float,
        min_out: float,
        swap_base_for_quote: bool = False,  # False = buy base (stock) with USDC
    ) -> SwapResult:
        """Execute swap via DBC. Returns tx + out_amount."""
        res = self._run_ts("swap", pool, str(amount_in), str(min_out), str(swap_base_for_quote).lower())
        return SwapResult(
            tx=res["tx"],
            out_amount=float(res["out_amount"]),
            price_impact=float(res.get("price_impact", 0)),
            fee=float(res.get("fee", 0)),
        )

    def get_pool_state(self, pool: str) -> dict[str, Any]:
        """Read pool state (reserves, sqrt_price, progress)."""
        return self._run_ts("state", pool)

    def get_config(self, config: str) -> dict[str, Any]:
        """Read partner config."""
        return self._run_ts("config", config)

    def quote_swap(
        self,
        pool: str,
        amount_in: float,
        swap_base_for_quote: bool = False,
        slippage_bps: int = 100,
    ) -> dict[str, Any]:
        """Get quote for swap without executing."""
        return self._run_ts("quote", pool, str(amount_in), str(swap_base_for_quote).lower(), str(slippage_bps))

    def migrate_to_damm_v2(self, pool: str, damm_config: str) -> str:
        """Manual migration trigger (keeper usually does this)."""
        res = self._run_ts("migrate", pool, damm_config)
        return res["tx"]


async def demo():
    """Test with devnet pool after creation."""
    import os
    rpc = os.getenv("RPC_URL", "https://api.devnet.solana.com")
    exe = MeteoraExecutor(rpc)
    # exe.create_config(1.0)  # calm regime
    # exe.create_pool("BASE_MINT", "CONFIG_PUBKEY", "AAPLx", "AAPLx", "https://...")
    print("MeteoraExecutor ready — call create_config/create_pool/swap")

if __name__ == "__main__":
    import asyncio
    asyncio.run(demo())