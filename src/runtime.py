"""Shared API/scheduler construction, with network I/O deferred to live startup.

Roles preserve package scope: the API retains its existing manager, whereas the
scheduler remains directional-only. This module does not establish cross-process
execution ownership; that is the next milestone, not a property of a shared path.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Callable, Literal

from .agent import AutonomousTradingAgent
from .assets import spot_companions, trade_assets
from .audit_logger import OnchainLogger
from .audit_trail import AuditLog
from .curator import CuratorAgent
from .data_integrity import DataIntegrityGate
from .exchange import create_exchange_client
from .execution import RiskGate
from .multi_leg import MultiLegExecutionManager
from .execution.ownership import OrderClaimStore
from .execution.intent_store import IntentStore
from .settings import Settings
from .strategy_lifecycle import LifecycleState, LifecycleStatus

RuntimeRole = Literal["api", "scheduler"]


class RuntimeSafetyError(RuntimeError):
    """The application's execution policy is absent, incomplete, or changed."""


class DeferredOnchainLogger:
    """Preserve the logger interface without probing RPC during module import."""

    def __init__(self, factory: Callable[[], OnchainLogger]):
        self._factory = factory
        self._logger: OnchainLogger | None = None
        self._lock = threading.Lock()

    def connect(self) -> OnchainLogger:
        with self._lock:
            if self._logger is None:
                self._logger = self._factory()
            return self._logger

    def __getattr__(self, name: str):
        return getattr(self.connect(), name)


def _configured_logger(settings: Settings) -> DeferredOnchainLogger | None:
    if not all((settings.xlayer_rpc_url.strip(),
                settings.audit_contract_address.strip(),
                settings.agent_wallet_private_key.strip())):
        return None
    return DeferredOnchainLogger(lambda: OnchainLogger(
        rpc_url=settings.xlayer_rpc_url,
        contract_address=settings.audit_contract_address,
        private_key=settings.agent_wallet_private_key,
        chain_id=settings.xlayer_chain_id,
    ))


def _risk_gate(settings: Settings) -> RiskGate:
    # Do not supply the deferred logger here: the constructor's optional sync
    # would otherwise turn application import into a network operation.
    return RiskGate(
        max_position_usd=settings.max_position_usd,
        max_daily_loss_usd=settings.max_daily_loss_usd,
        max_daily_trades=settings.max_daily_trades,
        max_daily_volume_usd=settings.max_daily_volume_usd,
        max_leverage=settings.max_leverage,
        min_confidence_bps=settings.min_confidence_bps,
        max_price_age_seconds=settings.max_price_age_seconds,
        max_slippage_pct=settings.max_slippage_pct,
        max_reference_velocity_pct_per_s=settings.max_reference_velocity_pct_per_s,
        loss_cooldown_minutes=settings.loss_cooldown_minutes,
        drawdown_window_days=settings.drawdown_window_days,
        drawdown_loss_mult=settings.drawdown_loss_mult,
        allowed_assets=trade_assets(),
        allowed_companions=spot_companions(),
        regime_throttle=settings.regime_throttle,
        regime_band_pct=settings.regime_band_pct,
        regime_size_scale=settings.regime_size_scale,
        dbc_curve_type=settings.dbc_curve_type,
        dbc_fee_bps=settings.dbc_fee_bps,
        dbc_graduation=settings.dbc_graduation,
        counters_durable=True,
        counter_store_path=settings.risk_state_path,
        counter_fail_closed=True,
    )


class TradingRuntime:
    """One application runtime; credentials never enter its public status."""

    def __init__(self, settings: Settings, role: RuntimeRole, *, cli=None,
                 dry_run: bool | None = None, agent_id: str | None = None):
        if role not in ("api", "scheduler"):
            raise ValueError("Unknown trading runtime role")
        self.role = role
        self.dry_run = settings.dry_run if dry_run is None else dry_run
        if not isinstance(self.dry_run, bool):
            raise RuntimeSafetyError("Runtime dry_run must be a boolean")
        if not self.dry_run and not settings.allow_live:
            raise RuntimeSafetyError("Live runtime requires ALLOW_LIVE=true")
        self.onchain_logger = _configured_logger(settings)
        if not self.dry_run and self.onchain_logger is None:
            raise RuntimeSafetyError("Live runtime requires configured onchain audit logging")
        # Configuration is copied so later edits to a caller's Settings object
        # cannot change the authorized mode underneath an initialized runtime.
        self._settings = settings.model_copy(deep=True)
        self._start_lock = threading.Lock()
        self._started = self.dry_run
        self.cli = cli if cli is not None else create_exchange_client(settings.exchange)
        self.risk_gate = _risk_gate(settings)
        self.audit_log = AuditLog(path=settings.audit_log_path)
        self.integrity_gate = DataIntegrityGate(
            staleness_threshold_s=settings.data_staleness_seconds,
        )
        profiles = Path(__file__).resolve().parent.parent / "config/profiles.yaml"
        # A missing policy file must not turn the scheduler into an unrestricted
        # agent. The repository ships this file as a required runtime input.
        self.curator = CuratorAgent(profiles, audit_log=self.audit_log)
        self.multi_leg_manager = (
            MultiLegExecutionManager(
                max_concurrent_packages=settings.max_concurrent_packages,
                persist_dir=settings.multi_leg_state_dir,
            ) if role == "api" else None
        )
        self.claim_store = OrderClaimStore(settings.execution_claim_dir)
        self.intent_store = IntentStore(settings.execution_intent_db)
        self.agent = AutonomousTradingAgent(
            okx_cli=self.cli,
            risk_gate=self.risk_gate,
            onchain_logger=self.onchain_logger,  # type: ignore[arg-type]
            dry_run=self.dry_run,
            max_position_usd=settings.max_position_usd,
            agent_id=agent_id if agent_id is not None else settings.agent_id,
            sizing_mode=settings.sizing_mode,
            kelly_fraction=settings.kelly_fraction,
            integrity_gate=self.integrity_gate,
            curator=self.curator,
            audit_log=self.audit_log,
            multi_leg_manager=self.multi_leg_manager,
            funding_arb_min_rate=settings.funding_arb_min_rate,
            regime_filter_window=settings.regime_filter_window,
            fpz_live_enabled=settings.fpz_live_enabled,
            fpz_sol_only=settings.fpz_sol_only,
            fpz_bypass_ensemble=settings.fpz_bypass_ensemble,
            fpz_confidence_floor_bps=settings.fpz_confidence_floor_bps,
            fpz_arb_package_priority=settings.fpz_arb_package_priority,
            fpz_arb_block_band_bps=settings.fpz_arb_block_band_bps,
            execution_guard=self.ensure_ready,
            claim_store=self.claim_store,
            intent_store=self.intent_store,
        )

    def start(self) -> None:
        """Initialize the mandatory live audit authority before accepting work."""
        with self._start_lock:
            if self._started:
                return
            if self.onchain_logger is None:
                raise RuntimeSafetyError("Required onchain audit logger is missing")
            self.onchain_logger.connect()
            self.risk_gate._onchain_logger = self.onchain_logger
            self.risk_gate.require_onchain_sync = True
            self.risk_gate.sync_with_onchain()
            self._started = True

    def ensure_ready(self) -> None:
        """Rechecked at cycle entry and immediately before order submission."""
        if (self.agent.dry_run is not self.dry_run
                or self.agent.executor.dry_run is not self.dry_run):
            raise RuntimeSafetyError("Execution mode changed after runtime construction")
        if not self._started:
            raise RuntimeSafetyError("Required live runtime startup has not completed")
        if not self.dry_run:
            if (self.onchain_logger is None
                    or self.agent.onchain_logger is not self.onchain_logger
                    or self.risk_gate._onchain_logger is not self.onchain_logger
                    or not self.risk_gate.require_onchain_sync):
                raise RuntimeSafetyError("Required onchain audit authority was detached")

    def status(self) -> dict:
        """Allowlisted effective configuration; no keys, endpoints, or tokens."""
        config = {
            "role": self.role,
            "dry_run": self.dry_run,
            "agent_id": self.agent.agent_id,
            "max_position_usd": self.risk_gate.max_position_usd,
            "max_daily_loss_usd": self.risk_gate.max_daily_loss_usd,
            "max_daily_trades": self.risk_gate.max_daily_trades,
            "max_daily_volume_usd": self.risk_gate.max_daily_volume_usd,
            "funding_arb_packages": self.multi_leg_manager is not None,
            "fpz_live_enabled": self.agent.fpz_live_enabled,
        }
        digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        return {**config, "runtime_initialized": self._started,
                "audit_configured": self.onchain_logger is not None,
                "configuration_digest": digest}

    def lifecycle_status(self) -> LifecycleStatus:
        """Expose runtime state through the shared strategy control plane."""
        return LifecycleStatus(
            f"trading-runtime:{self.role}",
            LifecycleState.RUNNING if self._started else LifecycleState.READY,
            self.dry_run,
            not self.dry_run,
            blocked_reason=None if self._started else "runtime startup pending",
        )


def build_runtime(settings: Settings, *, role: RuntimeRole, cli=None,
                  dry_run: bool | None = None, agent_id: str | None = None) -> TradingRuntime:
    return TradingRuntime(settings, role, cli=cli, dry_run=dry_run, agent_id=agent_id)
