"""Solana second rail for Tarstrade (ported from Stockulus).

EVM (X-Layer) logging is disabled for OKX DEX grant compliance
(.env.example). This module provides an opt-in Solana mirror that never
blocks the trade loop:

- ``SolanaSecondRail``: lazy, best-effort sync wrapper around the async
  ``SolanaAuditLogger``. Heavy deps (anchorpy/solders/solana) import inside
  methods so offline pytest stays green when they are absent.
- ``DualLogger``: duck-compatible with ``OnchainLogger`` (same
  ``log_decision`` / ``record_execution`` / ``agent_address`` surface).
  EVM stays authoritative: Solana failure never raises, only warns.
  Works EVM-only, Solana-only, both, or neither (None passthrough).

Env (all optional, rail stays OFF unless fully set + explicitly enabled):
  SOLANA_ENABLED=true
  SOLANA_RPC_URL=https://api.devnet.solana.com
  SOLANA_PROGRAM_ID=<TradeAuditTrail program id>
  SOLANA_KEYPAIR_PATH=<path to solana keypair json>

Never commit keys. Follows the repo rule: secrets from env/secrets only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from .chain_router import chain_for_asset, EVM as CHAIN_EVM, SOLANA as CHAIN_SOL
except Exception:  # pragma: no cover - router is stdlib-only, this is belt & braces
    CHAIN_EVM, CHAIN_SOL = "evm", "solana"

    def chain_for_asset(asset) -> str:
        return CHAIN_EVM

_ZERO_EVM = "0x" + "00" * 20
_ZERO_HASH = "0x" + "00" * 32


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def solana_config_from_env() -> dict[str, Any]:
    """Read Solana rail config from env. Disabled unless explicitly enabled."""
    return {
        "enabled": _env_flag("SOLANA_ENABLED"),
        "rpc_url": os.environ.get("SOLANA_RPC_URL", "").strip(),
        "program_id": os.environ.get("SOLANA_PROGRAM_ID", "").strip(),
        "keypair_path": os.environ.get("SOLANA_KEYPAIR_PATH", "").strip(),
    }


def load_solana_keypair(keypair_path: str):
    """Load a solders Keypair from a solana keypair file.

    Supports the standard ``[int, ... 64]`` JSON array and a raw hex secret
    (Stockulus demo format). Raises with a clear message otherwise.
    """
    from solders.keypair import Keypair

    raw = Path(keypair_path).read_text(encoding="utf-8").strip()
    try:
        data = json.loads(raw)
        if isinstance(data, list) and len(data) == 64:
            return Keypair.from_bytes(bytes(data))
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        secret = bytes.fromhex(raw)
        if len(secret) == 64:
            return Keypair.from_bytes(secret)
        if len(secret) == 32:
            return Keypair.from_seed(secret)
    except ValueError:
        pass
    raise ValueError(
        f"Unrecognized keypair format at {keypair_path}: "
        "expected 64-int JSON array or 32/64-byte hex"
    )


def _run_coro(coro_factory):
    """Run an async callable to completion from sync code, any thread context.

    The agent invokes loggers via ``asyncio.to_thread`` (no running loop in
    the worker thread) so ``asyncio.run`` is the fast path. If called from a
    thread that already runs a loop, fall back to a dedicated thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro_factory()).result()


class SolanaSecondRail:
    """Best-effort sync mirror of the Anchor TradeAuditTrail program.

    Never raises from public methods: misconfiguration returns None and
    per-call failures warn. The EVM logger (or dry-run gate) stays the
    authority on whether a trade proceeds.
    """

    def __init__(
        self,
        rpc_url: str = "",
        program_id: str = "",
        keypair_path: str = "",
        enabled: bool = False,
    ):
        self.rpc_url = rpc_url
        self.program_id = program_id
        self.keypair_path = keypair_path
        self.enabled = bool(enabled and rpc_url and program_id and keypair_path)
        if enabled and not self.enabled:
            logger.warning(
                "SolanaSecondRail enabled but misconfigured "
                "(need SOLANA_RPC_URL + SOLANA_PROGRAM_ID + SOLANA_KEYPAIR_PATH)"
            )

    @classmethod
    def from_env(cls) -> "SolanaSecondRail":
        cfg = solana_config_from_env()
        return cls(
            rpc_url=cfg["rpc_url"],
            program_id=cfg["program_id"],
            keypair_path=cfg["keypair_path"],
            enabled=cfg["enabled"],
        )

    @property
    def configured(self) -> bool:
        return self.enabled

    @property
    def agent_address(self) -> str | None:
        if not self.enabled:
            return None
        try:
            kp = load_solana_keypair(self.keypair_path)
            return str(kp.pubkey())
        except Exception as e:
            logger.warning(f"SolanaSecondRail address unavailable: {e}")
            return None

    def _connect(self):
        """Build + connect a SolanaAuditLogger (heavy imports stay local)."""
        from .audit_logger_sol import SolanaAuditLogger

        kp = load_solana_keypair(self.keypair_path)
        return SolanaAuditLogger(self.rpc_url, self.program_id, kp), kp

    def log_decision(self, payload) -> str | None:
        """Mirror a DecisionPayload to Solana. Returns tx or None. Never raises."""
        if not self.enabled:
            return None
        try:
            async def _do():
                logger_obj, _ = self._connect()
                await logger_obj.connect()
                try:
                    return await logger_obj.log_decision(
                        decision_id=str(payload.decision_id),
                        package_id=getattr(payload, "package_id", None),
                        asset=str(payload.asset),
                        signal=str(payload.signal),
                        strategy=str(payload.strategy),
                        confidence_bps=int(payload.confidence_bps),
                        entry_price=float(payload.entry_price or 0),
                        size_usd=float(payload.size_usd),
                        risk_hash=str(getattr(payload, "risk_params_hash", _ZERO_HASH)),
                    )
                finally:
                    await logger_obj.close()

            tx = _run_coro(_do)
            logger.info(f"Solana second-rail decision logged: {tx}")
            return tx
        except Exception as e:
            logger.warning(f"SolanaSecondRail log_decision mirror failed (non-blocking): {e}")
            return None

    def record_execution(
        self,
        decision_id: str,
        fill_price: float = 0,
        fill_size_usd: float = 0,
        fee_usd: float = 0,
        success: bool = True,
    ) -> str | None:
        if not self.enabled:
            return None
        try:
            async def _do():
                logger_obj, _ = self._connect()
                await logger_obj.connect()
                try:
                    return await logger_obj.record_execution(
                        decision_id=str(decision_id),
                        fill_price=float(fill_price or 0),
                        fill_size_usd=float(fill_size_usd or 0),
                        fee_usd=float(fee_usd or 0),
                        success=bool(success),
                    )
                finally:
                    await logger_obj.close()

            return _run_coro(_do)
        except Exception as e:
            logger.warning(f"SolanaSecondRail record_execution mirror failed (non-blocking): {e}")
            return None

    def is_kill_switch_active(self) -> bool | None:
        if not self.enabled:
            return None
        try:
            async def _do():
                logger_obj, _ = self._connect()
                await logger_obj.connect()
                try:
                    return await logger_obj.is_kill_switch_active()
                finally:
                    await logger_obj.close()

            return bool(_run_coro(_do))
        except Exception as e:
            logger.warning(f"SolanaSecondRail kill-switch read failed: {e}")
            return None


class DualLogger:
    """Chain-routed logger: the audit trail lives where the asset lives.

    ``chain_router.chain_for_asset`` picks the primary rail per decision
    (``AAPLx`` -> Solana, ``BTC-USDT-SWAP`` -> EVM). The other rail, when
    configured, gets a best-effort mirror after primary success.

    Drop-in for ``OnchainLogger``: ``agent.py`` calls ``log_decision`` /
    ``record_execution`` via ``asyncio.to_thread`` and reads
    ``agent_address`` — all preserved here. Either rail may be None:
    - primary missing -> RuntimeError (blocks, like an EVM log failure
      today: never trade without the asset's own audit trail).
    - mirror failure  -> warning only, never blocks.
    """

    _CHAIN_CACHE_CAP = 10000

    def __init__(self, evm=None, sol: SolanaSecondRail | None = None):
        self._evm = evm
        self._sol = sol if sol is not None and sol.enabled else None
        self._chains: dict[str, str] = {}  # decision_id -> primary chain

    def _rail_for(self, chain: str):
        return self._evm if chain == CHAIN_EVM else self._sol

    def _remember(self, chain: str, decision_id) -> None:
        try:
            if decision_id:
                if len(self._chains) >= self._CHAIN_CACHE_CAP:
                    self._chains.clear()
                self._chains[str(decision_id)] = chain
        except Exception:
            pass

    def chain_for(self, asset) -> str:
        """Primary audit chain for an asset symbol (the dropdown value)."""
        return chain_for_asset(asset)

    def audit_chain_for(self, decision_id: str) -> str | None:
        """Primary chain recorded for a past decision, if still cached."""
        return self._chains.get(str(decision_id))

    def rails_summary(self) -> dict:
        """Public rail status (no secrets): which audit rails are live."""
        return {
            "evm": {"configured": self._evm is not None},
            "solana": {"configured": self._sol is not None},
        }

    @property
    def agent_address(self) -> str:
        if self._evm is not None:
            try:
                return self._evm.agent_address
            except Exception:
                pass
        if self._sol is not None:
            addr = self._sol.agent_address
            if addr:
                return addr
        return _ZERO_EVM

    def log_decision(self, payload) -> str | None:
        asset = getattr(payload, "asset", None)
        decision_id = getattr(payload, "decision_id", "?")
        chain = chain_for_asset(asset)
        self._remember(chain, decision_id)
        primary = self._rail_for(chain)
        mirror_chain = CHAIN_SOL if chain == CHAIN_EVM else CHAIN_EVM
        mirror = self._rail_for(mirror_chain)
        if primary is None:
            have = [n for n, r in (("evm", self._evm), ("solana", self._sol)) if r is not None]
            raise RuntimeError(
                f"DualLogger: asset {asset!r} routes to {chain} but that rail "
                f"is not configured (have: {have or ['none']}). Refusing to "
                "trade without its audit trail."
            )
        tx = primary.log_decision(payload)  # raises on failure: blocks, as today
        if mirror is not None:
            try:
                mirror_tx = mirror.log_decision(payload)  # best-effort
            except Exception as e:
                logger.warning(f"DualLogger {mirror_chain} mirror failed (non-blocking): {e}")
                mirror_tx = None
            logger.info(
                "DualLogger decision %s primary=%s(%s) mirror=%s(%s)",
                decision_id, chain, tx, mirror_chain, mirror_tx,
            )
        else:
            logger.info("DualLogger decision %s primary=%s(%s)", decision_id, chain, tx)
        return tx

    def record_execution(self, **kwargs) -> None:
        decision_id = kwargs.get("decision_id", "")
        chain = self._chains.get(str(decision_id), CHAIN_EVM)
        ordered = [chain, CHAIN_SOL if chain == CHAIN_EVM else CHAIN_EVM]
        for i, c in enumerate(ordered):
            rail = self._rail_for(c)
            if rail is None:
                continue
            try:
                if c == CHAIN_EVM and rail is self._evm:
                    rail.record_execution(**kwargs)
                else:
                    rail.record_execution(
                        decision_id=kwargs.get("decision_id", ""),
                        fill_price=kwargs.get("fill_price", 0),
                        fill_size_usd=kwargs.get("fill_size_usd", 0),
                        fee_usd=kwargs.get("fee_usd", 0),
                        success=kwargs.get("success", True),
                    )
            except Exception as e:
                scope = "primary" if i == 0 else "mirror"
                logger.warning(f"DualLogger {c} {scope} record_execution failed: {e}")

    def is_kill_switch_active(self) -> bool:
        for rail in (self._evm, self._sol):
            if rail is None:
                continue
            try:
                if rail.is_kill_switch_active():
                    return True
            except Exception as e:
                logger.warning(f"DualLogger kill-switch read failed: {e}")
        return False

    def activate_kill_switch(self, reason: str = ""):
        if self._evm is not None:
            self._evm.activate_kill_switch(reason)
        if self._sol is not None:
            try:
                async def _do():
                    logger_obj, _ = self._sol._connect()
                    await logger_obj.connect()
                    try:
                        return await logger_obj.activate_kill_switch(reason or "manual")
                    finally:
                        await logger_obj.close()

                _run_coro(_do)
            except Exception as e:
                logger.warning(f"DualLogger Solana kill-switch activation failed: {e}")

    def deactivate_kill_switch(self):
        if self._evm is not None:
            self._evm.deactivate_kill_switch()
        if self._sol is not None:
            try:
                async def _do():
                    logger_obj, _ = self._sol._connect()
                    await logger_obj.connect()
                    try:
                        return await logger_obj.deactivate_kill_switch()
                    finally:
                        await logger_obj.close()

                _run_coro(_do)
            except Exception as e:
                logger.warning(f"DualLogger Solana kill-switch deactivation failed: {e}")

    def get_contract_stats(self, *args, **kwargs):
        if self._evm is not None:
            return self._evm.get_contract_stats(*args, **kwargs)
        return {}


def make_solana_rail() -> SolanaSecondRail | None:
    """Build the rail from env. Returns None when disabled (common case)."""
    rail = SolanaSecondRail.from_env()
    return rail if rail.enabled else None


def wrap_dual(evm, sol: SolanaSecondRail | None = None):
    """Wrap EVM logger + Solana rail. Returns evm untouched when no rail."""
    if sol is None or not sol.enabled:
        return evm
    if evm is None:
        return DualLogger(evm=None, sol=sol)
    return DualLogger(evm=evm, sol=sol)
