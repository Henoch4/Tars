"""
Multi-agent orchestrator for the autonomous trading agent.

Architecture (inspired by TradingAgents + Jim Simons' research factory):
  MarketDataAgent → SignalAgent → RiskAgent → ExecutionAgent → OnchainLogger

Each agent has a clear, specialized role. The orchestrator sequences them
and handles retries, timeouts, and error recovery.

This is the main entry point — the /hire endpoint calls run_trading_cycle().
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

from .signals import (
    Signal,
    mean_reversion_signal,
    momentum_signal,
    funding_rate_signal,
    funding_carry_signal,
    ml_funding_carry_signal,
    record_ml_degradation,
    ensemble_signal,
    backtest_simple,
)
from .trader import (
    TraderStrategy,
    MarketContext,
    MeanReversionTrader,
    MomentumTrader,
    FundingRateTrader,
    FundingCarryTrader,
    create_default_swarm,
)
from .consensus import ConsensusGate, ConsensusResult, ConsensusBehavior, create_default_consensus_gate
from .execution import (
    OrderExecutor,
    OrderRequest,
    OrderResult,
    OrderStatus,
    RiskGate,
    RiskCheckResult,
    ExecutionError,
)
from .audit_logger import OnchainLogger, DecisionPayload
from .okx_cli import OkxCli, OkxCliConfig, OkxCliError
from .audit_trail import AuditLog
from .curator import CuratorAgent, apply_env_overrides
from .data_integrity import DataIntegrityGate, IntegrityResult, MarketTick, Severity
from .multi_leg import MultiLegExecutionManager, Step, LiveFillSimulator, PackageState
from .reconciliation import read_okx_balance

logger = logging.getLogger(__name__)


@dataclass
class TradingCycleResult:
    """Result of a complete trading cycle."""
    cycle_id: str
    timestamp: float
    signals: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    executions: list[dict] = field(default_factory=list)
    total_pnl_usd: float = 0.0
    total_fees_usd: float = 0.0
    status: str = "completed"
    errors: list[str] = field(default_factory=list)
    curator: dict | None = None


class AutonomousTradingAgent:
    """
    Main orchestrator that coordinates the multi-agent trading pipeline.
    
    Usage:
        agent = AutonomousTradingAgent(
            okx_cli=OkxCli(OkxCliConfig(demo=True)),
            risk_gate=RiskGate(...),
            dry_run=True,
        )
        result = await agent.run_trading_cycle(assets=["BTC-USDT-SWAP"])
    """

    def __init__(
        self,
        okx_cli: OkxCli,
        risk_gate: RiskGate,
        onchain_logger: OnchainLogger | None = None,
        dry_run: bool = True,
        max_position_usd: float = 5000,
        agent_id: str = "autonomous-trader-001",
        enable_momentum: bool = False,
        sizing_mode: str = "kelly",
        kelly_fraction: float = 0.5,
        integrity_gate: DataIntegrityGate | None = None,
        curator: CuratorAgent | None = None,
        audit_log: AuditLog | None = None,
        expected_equity: float | None = None,
        multi_leg_manager: MultiLegExecutionManager | None = None,
        funding_arb_min_rate: float = 0.001,
        # Z1 fee schedule for the carry break-even gate (per-leg taker
        # fees in bps, slippage per leg per side in bps, expected hold in
        # funding periods). The package requires funding above BOTH the
        # flat floor above and the computed break-even.
        carry_taker_fee_bps_spot: float = 5.0,
        carry_taker_fee_bps_perp: float = 5.0,
        carry_slippage_bps: float = 3.0,
        carry_hold_periods: float = 21.0,
        regime_filter_window: int = 0,
        # ML-enhanced funding carry gate (tars-lora): replaces fixed threshold
        # with learned "will 7d carry clear costs?" decision
        use_ml_carry_gate: bool = False,
        # --- Product B: Swarm Trading (ConsensusGate + TraderStrategy) ---
        use_consensus_gate: bool = False,
        consensus_gate: ConsensusGate | None = None,
        traders: list[TraderStrategy] | None = None,
    ):
        self.cli = okx_cli
        self.risk_gate = risk_gate
        self.onchain_logger = onchain_logger
        self.dry_run = dry_run
        self.max_position_usd = max_position_usd
        self.agent_id = agent_id
        self.sizing_mode = sizing_mode
        self.kelly_fraction = kelly_fraction
        # Off by default — see _generate_signals docstring. Momentum is a
        # phase-2+ strategy per the design doc's Section 0 MVP scope.
        self.enable_momentum = enable_momentum

        # Structural-trend guard (roadmap Phase 2 regime filter): suppresses
        # mean-reversion LONGs in sustained downtrends (and vice versa) and
        # requires momentum crossovers to agree with the longer horizon.
        # 0 disables. main.py wires REGIME_FILTER_WINDOW (default 50).
        self.regime_filter_window = regime_filter_window

        # The delta-neutral funding-arbitrage package machinery. When a
        # manager is wired, an asset whose funding rate clears
        # `funding_arb_min_rate` (default 0.001 = 0.1%) runs the long-spot /
        # short-perp package path INSTEAD of the directional signal path for
        # that cycle. None disables the feature — every test that doesn't
        # wire a manager keeps the existing directional behavior.
        # 
        # ML enhancement: tars-lora model replaces the fixed threshold as the
        # "open/close brain" for the delta-neutral package. When
        # `use_ml_carry_gate` is True, the ML model's "will 7d carry clear
        # costs?" decision gates the package instead of the fixed rate threshold.
        self.multi_leg_manager = multi_leg_manager
        self.funding_arb_min_rate = funding_arb_min_rate
        self.carry_taker_fee_bps_spot = carry_taker_fee_bps_spot
        self.carry_taker_fee_bps_perp = carry_taker_fee_bps_perp
        self.carry_slippage_bps = carry_slippage_bps
        self.carry_hold_periods = carry_hold_periods
        self.use_ml_carry_gate = use_ml_carry_gate
        self._ml_carry_client = None
        # S5/W3: if the gate was requested but the client failed to build,
        # the flag STAYS on and the gate BLOCKS (counted) — it never silently
        # degrades to the fixed threshold the operator explicitly replaced.
        self._ml_carry_init_error: str | None = None
        # Rolling history for ML features: per-asset funding rates and perp prices
        # across cycles. Max 672 entries = 8 entries/day * 7 days * 3 cycles/day.
        # Sufficient for 7-day mean and z-score computation without unbounded growth.
        self._ml_history: dict[str, dict] = {}
        self._ML_HISTORY_MAX = 672
        if use_ml_carry_gate:
            try:
                from .ml_inference import get_tars_lora_client
                self._ml_carry_client = get_tars_lora_client()
            except Exception as e:
                self._ml_carry_init_error = f"ml_client_init:{type(e).__name__}"
                record_ml_degradation(self._ml_carry_init_error)
                logger.warning(
                    "ML carry gate requested but client failed to initialize "
                    f"({e}); gate will BLOCK, threshold fallback disabled."
                )

        # Pre-signal integrity gate (runs BEFORE signal generation), curator
        # profile selector, and the local append-only audit log. All optional
        # and fail-open only in the sense that absence disables the feature —
        # when present, a HARD_BLOCK blocks the asset for the whole cycle.
        self.integrity_gate = integrity_gate
        self.curator = curator
        self.audit_log = audit_log
        self.expected_equity = expected_equity

        # Resolved curator knobs for the current cycle, forwarded to sizing /
        # signal filtering. Defaults (no curator, or knob untouched by env)
        # are neutral: multiplier 1.0, no extra confidence floor, all signals.
        self._position_size_multiplier: float = 1.0
        self._confidence_floor_bps: int | None = None
        self._enabled_signals: set[str] | None = None

        # --- Product B: Swarm Trading ---
        self.use_consensus_gate = use_consensus_gate
        self.consensus_gate = consensus_gate or create_default_consensus_gate()
        self.traders = traders or create_default_swarm()
        # Group traders by cohort for consensus
        self._traders_by_cohort: dict[str, list[TraderStrategy]] = {}
        for trader in self.traders:
            self._traders_by_cohort.setdefault(
                trader.config.asset_class_cohort, []
            ).append(trader)

        self.executor = OrderExecutor(
            cli=okx_cli,
            risk_gate=risk_gate,
            dry_run=dry_run,
            agent_id=agent_id,
        )

        # Concurrency guards for the shared, mutable pieces touched inside the
        # parallel per-asset pipeline: the risk gate's regime/volume state and
        # the append-only audit log. Network I/O (onchain writes, OKX fills)
        # runs in worker threads via asyncio.to_thread and is serialized only
        # on the nonce lock inside OnchainLogger.
        self._risk_lock = asyncio.Lock()
        self._audit_lock = asyncio.Lock()

        # Track open positions for this agent
        self._open_positions: dict[str, dict] = {}
        self._daily_pnl: float = 0.0

        # Equity-delta loss feed (roadmap Phase 1: the daily-loss limit must be
        # fed by reality, not left inert). Holds the last observed OKX total
        # equity; a negative delta between consecutive live cycles is reported
        # to the risk gate as a realized loss. None = no baseline yet.
        self._last_cycle_equity: float | None = None

    async def _fetch_market_data(self, asset: str, lookback: int = 50) -> dict:
        """Fetch market data: price history, funding rate, current position."""
        # Fetch recent trade data
        try:
            trades = await self.cli.run(
                "market", "trades",
                asset,
                "--limit", str(lookback),
                use_global_flags=False,
            )
        except OkxCliError as e:
            logger.warning(f"Failed to fetch trades for {asset}: {e}")
            trades = []

        # Fetch funding rate
        try:
            funding = await self.cli.run(
                "market", "funding-rate",
                asset,
                use_global_flags=False,
            )
        except OkxCliError:
            funding = [{"fundingRate": "0"}]

        # Fetch current position
        position = await self.executor.get_position(asset)

        # okx CLI 1.4.4 returns JSON arrays directly for market data, not {"data": [...]}
        trade_list = trades if isinstance(trades, list) else trades.get("data", []) if isinstance(trades, dict) else []
        if isinstance(funding, list):
            funding_rate = float(funding[0].get("fundingRate", "0")) if funding else 0.0
        elif isinstance(funding, dict):
            funding_rate = float(funding.get("data", [{}])[0].get("fundingRate", "0"))
        else:
            funding_rate = 0.0

        return {
            "asset": asset,
            "trades": trade_list,
            "funding_rate": funding_rate,
            "position": position,
            "timestamp": time.time(),
        }

    def _extract_prices(self, market_data: dict) -> list[float]:
        """Extract close prices from trade data.

        Returns [] when there is no real price data — never a fabricated
        price. The risk gate's freshness check treats an absent price as
        NO_PRICE_REFERENCE and refuses to trade; a fake 1.0 fallback here
        would quietly defeat that gate.
        """
        prices = []
        for trade in market_data.get("trades", []):
            try:
                px = float(trade.get("px", 0))
                if px > 0:
                    prices.append(px)
            except (ValueError, TypeError):
                continue
        return prices

    def _extract_price_data(self, market_data: dict) -> list[dict]:
        """Extract structured price data (close + volume) for momentum signal."""
        price_data = []
        for trade in market_data.get("trades", []):
            try:
                px = float(trade.get("px", 0))
                if px <= 0:
                    continue
                price_data.append({
                    "close": px,
                    "volume": float(trade.get("sz", 0)),
                })
            except (ValueError, TypeError):
                continue
        # No fabricated fallback: an empty series returns []. Momentum on a
        # fake flat $1.0 series would launder garbage into the ensemble —
        # the caller skips the strategy instead (see _generate_signals).
        return price_data

    def _get_ml_history(self, asset: str) -> dict:
        """Get rolling ML history for an asset. Creates if missing."""
        if asset not in self._ml_history:
            self._ml_history[asset] = {"funding": [], "prices": []}
        return self._ml_history[asset]

    def _update_ml_history(self, asset: str, funding_rate: float, perp_price: float | None) -> None:
        """Append to rolling history, evicting oldest if over max size."""
        hist = self._get_ml_history(asset)
        hist["funding"].append(funding_rate)
        if perp_price is not None:
            hist["prices"].append(perp_price)
        # Trim to max size
        if len(hist["funding"]) > self._ML_HISTORY_MAX:
            hist["funding"] = hist["funding"][-self._ML_HISTORY_MAX:]
        if len(hist["prices"]) > self._ML_HISTORY_MAX:
            hist["prices"] = hist["prices"][-self._ML_HISTORY_MAX:]

    def _resolve_curator_profile(self) -> dict | None:
        """Resolve the active curator profile for this cycle.

        Default-passthrough: the profile supplies the default for every knob,
        and an operator-set env var for a knob wins only when explicitly set
        (see apply_env_overrides). Returns the resolved profile dict (or None
        when no curator is wired) and stores the cycle's sizing knobs.
        """
        if not self.curator:
            self._position_size_multiplier = 1.0
            self._confidence_floor_bps = None
            self._enabled_signals = None
            return None

        profile = self.curator.active_profile()
        resolved = apply_env_overrides(
            profile,
            {
                "position_size_multiplier": os.getenv("CURATOR_POSITION_SIZE_MULTIPLIER"),
                "confidence_floor_bps": os.getenv("CURATOR_CONFIDENCE_FLOOR_BPS"),
                "max_leverage": os.getenv("CURATOR_MAX_LEVERAGE"),
            },
            casters={
                "position_size_multiplier": float,
                "confidence_floor_bps": int,
                "max_leverage": float,
            },
        )
        self._position_size_multiplier = float(resolved.get("position_size_multiplier", 1.0))
        self._confidence_floor_bps = resolved.get("confidence_floor_bps")
        enabled = resolved.get("enabled_signals")
        self._enabled_signals = set(enabled) if enabled else None
        return resolved

    def _check_integrity(self, asset: str, market_data: dict,
                         ledger: dict | None = None) -> IntegrityResult | None:
        """Run the pre-signal integrity gate for an asset.

        Returns None when no gate is wired. A HARD_BLOCK result means the
        asset is skipped before a single signal is generated on top of
        questionable inputs. The ledger-consistency check only runs when real
        book figures are supplied (`ledger={"cash":..., "positions_value":...}`
        plus self.expected_equity) — fabricating zeros would defeat the whole
        point of the check.
        """
        if not self.integrity_gate:
            return None

        now_s = time.time()
        tick = MarketTick(
            timestamp=float(market_data.get("timestamp", now_s)),
            funding_rate=float(market_data.get("funding_rate", 0.0)),
        )
        results = [
            self.integrity_gate.check_market_data({asset: tick}, now_s=now_s),
        ]
        if ledger and self.expected_equity is not None:
            results.append(
                self.integrity_gate.check_ledger_consistency(
                    cash=float(ledger.get("cash", 0.0)),
                    positions_value=float(ledger.get("positions_value", 0.0)),
                    expected_equity=self.expected_equity,
                )
            )
        return self.integrity_gate.combine(*results)

    def _generate_signals(
        self, asset: str, market_data: dict, spot_price: float | None = None
    ) -> list[Signal]:
        """Generate signals from all enabled strategies.

        Design-doc note (Section 0): momentum is a directional strategy the
        design doc explicitly excluded from MVP scope — it's harder to defend
        against a bad directional call than the market-neutral funding-arb
        thesis. It's kept available here for phase 2 but disabled by default;
        enable via self.enable_momentum only once that phase's graduation
        criteria (Section 7) are actually met.

        Also note: funding_rate_signal below is a directional contrarian bet
        on funding-rate extremes reverting — it is NOT the delta-neutral
        long-spot/short-perp funding arbitrage described in the design doc's
        Section 0 thesis. That two-leg hedged package IS implemented
        separately (_run_funding_arb_package + multi_leg.py) and runs instead
        of this signal when FUNDING_ARB_MIN_RATE is met; this naive signal
        still carries real directional risk whenever it fires outside that
        path and should be sized and reasoned about accordingly, not treated
        as market-neutral.

        ML enhancement: ml_funding_carry_signal uses tars-lora model to predict
        "will 7d carry clear costs?" — replaces fixed threshold with learned gate.
        """
        prices = self._extract_prices(market_data)
        price_data = self._extract_price_data(market_data)
        funding_rate = market_data.get("funding_rate", 0.0)

        signals = [
            mean_reversion_signal(
                asset, prices, window=20, z_threshold=2.0,
                regime_window=self.regime_filter_window,
            ),
            funding_rate_signal(asset, funding_rate, threshold=0.001),
        ]

        # ML-enhanced funding carry signal (delta-neutral)
        if spot_price and prices:
            perp_price = prices[-1]
            # Use rolling history for 7-day mean and z-score features
            hist = self._get_ml_history(asset)
            funding_history = hist["funding"] if hist["funding"] else [funding_rate]
            price_history = hist["prices"] if hist["prices"] else (prices[-20:] if len(prices) >= 20 else prices)
            signals.append(
                ml_funding_carry_signal(
                    asset=asset,
                    spot_price=spot_price,
                    perp_price=perp_price,
                    funding_rate=funding_rate,
                    funding_history=funding_history,
                    price_history=price_history,
                )
            )

        if self.enable_momentum and price_data:
            signals.append(
                momentum_signal(
                    asset, price_data, short_window=5, long_window=20,
                    regime_window=self.regime_filter_window,
                )
            )

        # Curator profile's enabled-signal allowlist: strategies not in the
        # active profile are dropped before they reach the ensemble.
        if self._enabled_signals is not None:
            signals = [s for s in signals if s.strategy in self._enabled_signals]

        return signals

    def _compute_order_size(self, signal: Signal) -> float:
        """Compute order size in USD from confidence using fractional Kelly.

        Kelly criterion for even-money (b=1) bets: f* = 2p - 1, the share of
        the bankroll that maximizes long-run geometric growth. We scale the
        full-Kelly stake by `kelly_fraction` (default 0.5 = half-Kelly), the
        standard conservative choice: half-Kelly gives ~75% of full-Kelly's
        growth at ~25% of its drawdown variance, per the original 1956 Kelly
        sizing literature.

            f = (2p - 1) * kelly_fraction      where p = signal.probability
            size_usd = max_position_usd * f

        This replaces the older linear sizing (size = max * p), which
        overbet weak signals: a 55% signal spent 55% of max. Under half-Kelly
        the same signal spends (2*0.55 - 1)*0.5 = 5% of max. Signals below
        50% confidence produce a negative edge and size 0 (no trade), which
        is exactly the desired filter.
        """
        p = signal.confidence_bps / 10000.0
        if not (0.0 <= p <= 1.0):
            p = 0.0
        if self.sizing_mode == "linear":
            base = self.max_position_usd * p
        else:
            edge = 2.0 * p - 1.0
            if edge <= 0.0:
                return 0.0
            base = self.max_position_usd * edge * self.kelly_fraction
        # Curator profile position-size multiplier (default 1.0 = unchanged).
        return base * self._position_size_multiplier

    def _signal_to_order(
        self, signal: Signal, market_data: dict
    ) -> OrderRequest | None:
        """Convert a signal into an order request, or None if not tradeable."""
        if not signal.is_tradeable:
            return None

        position = market_data.get("position")
        current_size = float(position.get("pos", 0)) if position else 0
        current_side = position.get("side") if position else None

        # Determine order size from confidence via fractional Kelly sizing
        order_size = self._compute_order_size(signal)
        if order_size <= 0.0:
            logger.info(
                f"No order for {signal.asset}: {signal.direction} "
                f"(conf {signal.confidence_bps}/10000 has no positive Kelly edge)"
            )
            return None

        # If we have a position in the same direction, consider it a hold
        if current_side and abs(current_size) > 0:
            direction_map = {"long": "LONG", "short": "SHORT"}
            if direction_map.get(current_side.lower()) == signal.direction:
                logger.info(
                    f"Already {current_side} {current_size} {signal.asset}. "
                    f"Skipping entry, considering add/reduce."
                )
                # For now, skip — don't double down
                return None

        side_map: dict[str, Literal["buy", "sell"]] = {"LONG": "buy", "SHORT": "sell"}

        return OrderRequest(
            inst_id=signal.asset,
            side=side_map[signal.direction],
            order_type="market",
            size=f"{order_size:.2f}",
            client_oid=f"signal_{signal.strategy}_{uuid.uuid4().hex[:8]}",
            # Entries are never reduce-only: reduceOnly on a SHORT entry is
            # rejected by OKX (or silently reduces an existing long instead
            # of opening the modeled short). Position reduction happens via
            # the unwind path, not by mislabeling entries.
            reduce_only=False,
            confidence_bps=signal.confidence_bps,
        )

    def _funding_arb_opportunity(
        self, asset: str, market_data: dict, spot_price: float | None = None, perp_price: float | None = None
    ) -> bool:
        """Is this asset a candidate for a delta-neutral funding-arb package?

        Positive funding only: the package is long-spot / short-perp, which
        COLLECTS funding when longs pay shorts. The mirror (negative funding,
        short spot is not expressible) is out of scope — see the summary in
        the module docstring. Requires a wired manager with capacity and no
        already-open package on the same asset.

        When `use_ml_carry_gate` is True, the tars-lora model's "will 7d carry
        clear costs?" decision replaces the fixed `funding_arb_min_rate` threshold.

        S5/W3: with the gate on, every ML-unavailable condition (client init
        failure, missing spot/perp prices, degraded signal) BLOCKS the package
        with a counted, typed reason. It never falls through to the threshold
        the operator explicitly replaced — that would defeat the gate silently.
        """
        if self.multi_leg_manager is None:
            return False

        funding_rate = float(market_data.get("funding_rate", 0.0))

        # ML-enhanced gate: use tars-lora model decision
        if self.use_ml_carry_gate:
            if self._ml_carry_client is None:
                record_ml_degradation(
                    f"{asset}:{self._ml_carry_init_error or 'ml_client_missing'}"
                )
                logger.info(f"ML carry gate blocked {asset}: client unavailable")
                return False
            if not (spot_price and perp_price):
                record_ml_degradation(f"{asset}:ml_missing_prices")
                logger.info(
                    f"ML carry gate blocked {asset}: no spot/perp price "
                    "(threshold fallback disabled while gate is on)"
                )
                return False
            # Use rolling history for 7-day mean and z-score features
            hist = self._get_ml_history(asset)
            funding_history = hist["funding"] if hist["funding"] else [funding_rate]
            price_history = hist["prices"] if hist["prices"] else self._extract_prices(market_data)

            # Use the ML signal function directly
            from .signals import ml_funding_carry_signal
            ml_signal = ml_funding_carry_signal(
                asset=asset,
                spot_price=spot_price,
                perp_price=perp_price,
                funding_rate=funding_rate,
                funding_history=funding_history,
                price_history=price_history,
            )
            # A degraded signal (no ML prediction) blocks, explicitly —
            # the direction check below would also block it, but the
            # reason must name the degradation, not "model says NO".
            if ml_signal.metadata.get("degraded"):
                record_ml_degradation(
                    f"{asset}:{ml_signal.metadata.get('degradation_reason', 'ml_degraded')}"
                )
                logger.info(
                    f"ML carry gate blocked {asset}: "
                    f"degraded ({ml_signal.metadata.get('degradation_reason')})"
                )
                return False
            # ML signal says LONG = carry will clear costs
            if ml_signal.direction != "LONG":
                logger.info(
                    f"ML carry gate blocked {asset}: "
                    f"model says NO (conf={ml_signal.confidence_bps/100:.0f}%)"
                )
                return False
            logger.info(
                f"ML carry gate approved {asset}: "
                f"model says YES (conf={ml_signal.confidence_bps/100:.0f}%)"
            )
        # Fixed threshold still applies on the approval path (unchanged
        # original semantics: ML approval is necessary but not sufficient).
        # It is never reached as a silent fallback for a failed ML gate —
        # every failure above returns False first.
        # Z1: the package must ALSO clear the fee-derived break-even. The
        # binding bound is the max of the flat floor and the computed edge,
        # so the gate only ever tightens, never loosens.
        from .signals import carry_break_even_rate
        break_even = carry_break_even_rate(
            taker_fee_bps_spot=self.carry_taker_fee_bps_spot,
            taker_fee_bps_perp=self.carry_taker_fee_bps_perp,
            slippage_bps_per_leg=self.carry_slippage_bps,
            hold_periods=self.carry_hold_periods,
        )
        required = max(self.funding_arb_min_rate, break_even)
        if funding_rate < required:
            bound = "break-even" if break_even >= self.funding_arb_min_rate else "floor"
            logger.info(
                f"Funding arb for {asset} blocked: funding {funding_rate:.6f} "
                f"below {bound} {required:.6f} "
                f"(floor={self.funding_arb_min_rate:.6f}, "
                f"break-even={break_even:.6f})"
            )
            return False

        allowed, reason = self.multi_leg_manager.can_open(asset)
        if not allowed:
            logger.info(f"Funding arb for {asset} skipped: {reason}")
            return False
        return True

    async def _run_funding_arb_package(
        self, asset: str, md: dict, cycle_id: str, out: dict
    ) -> dict:
        """Build, risk-gate, log and execute one delta-neutral funding-arb
        package for an asset. Mutates and returns `out` so the caller can
        return it directly (one trade per asset per cycle).

        Package: buy spot (BTC-USDT) + short perp (BTC-USDT-SWAP), 50/50.
        Both legs are risk-gated BEFORE the package is proposed; both legs
        are logged onchain with the SAME package_id BEFORE dispatch (the
        onchain log is the hard gate). Dispatch then runs through the shared
        OrderExecutor via LiveFillSimulator — the same fill verification,
        slippage collar and freshness gate the directional path uses, so the
        package can't outrun the risk controls by taking a second order path.
        """
        manager = self.multi_leg_manager
        if manager is None:
            out["errors"].append(
                f"Funding arb for {asset}: no MultiLegExecutionManager wired"
            )
            return out

        spot_inst = asset.replace("-SWAP", "")
        if spot_inst == asset:
            msg = f"Funding arb for {asset}: no spot pair (not a -SWAP perp)"
            out["errors"].append(msg)
            return out

        funding_rate = float(md.get("funding_rate", 0.0))
        perp_prices = self._extract_prices(md)
        perp_price = perp_prices[-1] if perp_prices else None

        # Spot leg needs its own reference price + timestamp for the risk
        # gate's freshness check (check 9) and post-fill verification — the
        # perp snapshot does not contain the spot instrument's data.
        try:
            spot_md = await self._fetch_market_data(spot_inst)
        except OkxCliError as e:
            out["errors"].append(f"Funding arb for {asset}: spot data fetch failed: {e}")
            return out
        spot_prices = self._extract_prices(spot_md)
        spot_price = spot_prices[-1] if spot_prices else None
        spot_timestamp = float(spot_md.get("timestamp", md.get("timestamp", 0)))
        perp_timestamp = float(md.get("timestamp", 0))

        if spot_price is None or perp_price is None:
            msg = f"Funding arb for {asset}: no spot ({spot_price}) or perp ({perp_price}) price"
            out["errors"].append(msg)
            return out

        # Confidence from funding extremity — same mapping as the directional
        # funding_rate_signal (0.1% -> 70%, 0.3%+ -> 90% cap), so package size
        # grows with the size of the payment being harvested.
        confidence = min(0.60 + abs(funding_rate) * 100, 0.90)
        confidence_bps = int(confidence * 10000)
        arb_signal = Signal(
            strategy="funding_arbitrage",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=confidence_bps,
            entry_price=perp_price,
        )
        notional = self._compute_order_size(arb_signal)
        if notional <= 0:
            out["errors"].append(
                f"Funding arb for {asset}: funding {funding_rate:.4f} "
                f"gives no positive Kelly edge"
            )
            return out

        leg_notional = notional / 2.0
        # Step collar is a fraction (0.003 = 0.3%); the gate's collar is a
        # percentage. Convert so both legs carry the same cap in like units.
        step_collar = min(self.risk_gate.max_slippage_pct / 100.0, 0.05)
        steps = [
            Step(venue="okx", action="short_perp", asset=asset,
                 amount_ratio=0.5, max_slippage_pct=step_collar),
            Step(venue="okx", action="buy_spot", asset=spot_inst,
                 amount_ratio=0.5, max_slippage_pct=step_collar),
        ]

        # Risk-gate BOTH legs before the package is proposed — the whole
        # planned trade, not each leg independently. Same agent, same
        # per-asset reference price/timestamp.
        perp_order = OrderRequest(
            inst_id=asset, side="sell", order_type="market",
            size=f"{leg_notional:.2f}", reduce_only=False,
            confidence_bps=confidence_bps,
        )
        spot_order = OrderRequest(
            inst_id=spot_inst, side="buy", order_type="market",
            size=f"{leg_notional:.2f}", reduce_only=False,
            confidence_bps=confidence_bps,
        )
        checks = [
            self.risk_gate.check_order(
                perp_order, self.agent_id,
                current_price=perp_price,
                current_price_timestamp=perp_timestamp,
                current_position_side=(md.get("position") or {}).get("side"),
                # Pre-flight only: the executor re-checks and counts each leg
                # at submission (place_order). Counting here would charge the
                # agent's daily quota for two orders at proposal time —
                # before either leg is dispatched, even if the package aborts.
                count_trade=False,
            ),
            self.risk_gate.check_order(
                spot_order, self.agent_id,
                current_price=spot_price,
                current_price_timestamp=spot_timestamp,
                count_trade=False,
            ),
        ]
        for check in checks:
            if not check.approved:
                from .metrics import inc as _metrics_inc
                _metrics_inc("tars_risk_rejections_total", {"code": check.code})
                out["errors"].append(f"Funding arb for {asset}: risk gate rejected: {check.reason}")
                logger.warning(f"Funding arb risk gate rejected {asset}: {check.code}: {check.reason}")
                return out

        try:
            pkg = manager.propose_package(steps, notional)
        except (ValueError, RuntimeError) as e:
            out["errors"].append(f"Funding arb for {asset}: package rejected: {e}")
            return out

        package_id = f"pkg_{uuid.uuid4().hex[:10]}"
        leg_payloads = [
            DecisionPayload(
                decision_id=f"dec_{uuid.uuid4().hex[:12]}",
                agent_address=self.onchain_logger.agent_address if self.onchain_logger else "0x" + "00" * 20,
                asset=asset,
                signal="SHORT",
                strategy="funding_arbitrage",
                confidence_bps=confidence_bps,
                entry_price=perp_price,
                size_usd=leg_notional,
                risk_params_hash=self.risk_gate.compute_risk_hash() if hasattr(self.risk_gate, 'compute_risk_hash') else "0x" + "00" * 32,
                timestamp=int(time.time()),
                is_short=True,
                package_id=package_id,
            ),
            DecisionPayload(
                decision_id=f"dec_{uuid.uuid4().hex[:12]}",
                agent_address=self.onchain_logger.agent_address if self.onchain_logger else "0x" + "00" * 20,
                asset=spot_inst,
                signal="LONG",
                strategy="funding_arbitrage",
                confidence_bps=confidence_bps,
                entry_price=spot_price,
                size_usd=leg_notional,
                risk_params_hash=self.risk_gate.compute_risk_hash() if hasattr(self.risk_gate, 'compute_risk_hash') else "0x" + "00" * 32,
                timestamp=int(time.time()),
                is_short=False,
                package_id=package_id,
            ),
        ]

        # Phase 4 (package): log BOTH legs onchain before any order is placed.
        # If either log fails the whole package is blocked — a half-logged
        # package would break the on-chain linkage the package_id provides.
        log_txs: list[str | None] = [None, None]
        for i, payload in enumerate(leg_payloads):
            if self.onchain_logger and not self.dry_run:
                try:
                    log_txs[i] = await asyncio.to_thread(
                        self.onchain_logger.log_decision, payload
                    )
                except Exception as e:
                    out["errors"].append(f"Funding arb for {asset}: onchain log failed: {e}")
                    logger.error(f"Funding arb onchain logging failed: {e}")
                    return out
            out["decisions"].append({
                "decision_id": payload.decision_id,
                "package_id": package_id,
                "asset": payload.asset,
                "tx_hash": log_txs[i],
                "signal": payload.signal,
                "confidence_bps": payload.confidence_bps,
                "confidence": payload.confidence_bps / 10000.0,
                "side": perp_order.side if i == 0 else spot_order.side,
                "size_usd": leg_notional,
                "risk_hash": payload.risk_params_hash,
                "status": "approved",
                "dry_run": self.dry_run or self.onchain_logger is None,
            })

        # Phase 5 (package): dispatch both legs through the shared executor,
        # then resolve. Both dispatch and resolve run real orders (or the
        # dry-run simulation) inside LiveFillSimulator's asyncio.run, which
        # can't run inside this event loop — run them in a worker thread.
        fill_sim = LiveFillSimulator(
            self.executor,
            reference_prices={
                asset: perp_price,
                spot_inst: spot_price,
            },
            reference_timestamps={
                asset: perp_timestamp,
                spot_inst: spot_timestamp,
            },
        )

        def _dispatch_and_resolve():
            manager.dispatch_concurrent(pkg, fill_sim)
            return manager.resolve(pkg, fill_sim)

        await asyncio.to_thread(_dispatch_and_resolve)

        if pkg.state == PackageState.LOCKED:
            await asyncio.to_thread(manager.settle, pkg)
            leg_fill_prices = {
                r.step.asset: r.fill_price for r in pkg.leg_results if r.filled
            }
            for i, payload in enumerate(leg_payloads):
                fill_price = leg_fill_prices.get(payload.asset)
                out["executions"].append({
                    "decision_id": payload.decision_id,
                    "package_id": package_id,
                    "asset": payload.asset,
                    "order_id": f"pkg_{pkg.id}",
                    "state": "filled",
                    "fill_px": fill_price,
                    "fill_price": fill_price,
                    "slippage_pct": None,
                    "size_usd": leg_notional,
                    "tx_hash": log_txs[i],
                    "status": "success",
                    "fee": "0",
                    "fee_ccy": "USDT",
                })
                if log_txs[i] and self.onchain_logger:
                    await asyncio.to_thread(
                        self.onchain_logger.record_execution,
                        decision_id=payload.decision_id,
                        fill_price=float(fill_price or 0),
                        fill_size_usd=leg_notional,
                        fee_usd=0,
                        success=True,
                    )
                async with self._risk_lock:
                    self.risk_gate.report_volume(self.agent_id, leg_notional)
            logger.info(f"Funding arb package {pkg.id} LOCKED and settled for {asset}")
        else:
            out["errors"].append(
                f"Funding arb for {asset}: package {pkg.id} ended {pkg.state.value} "
                f"(unwound={pkg.unwound})"
            )

        if self.audit_log:
            async with self._audit_lock:
                self.audit_log.write("funding_arb_package", {
                    "cycle_id": cycle_id,
                    "asset": asset,
                    "package_id": package_id,
                    "package_state": pkg.state.value,
                    "notional_usd": notional,
                    "funding_rate": funding_rate,
                    "slippage_breached": pkg.slippage_breached,
                })
        return out

    async def run_trading_cycle(self, assets: list[str]) -> TradingCycleResult:
        """
        Run a complete trading cycle:
        1. Fetch market data for all assets
        2. Generate signals
        3. Pass signals through risk engine
        4. Log decisions onchain (if configured)
        5. Execute approved orders
        6. Return results
        """
        cycle_id = f"cycle_{uuid.uuid4().hex[:10]}"
        result = TradingCycleResult(
            cycle_id=cycle_id,
            timestamp=time.time(),
        )
        # Pin loss attribution to the UTC day this cycle STARTED in, so a
        # cycle straddling midnight books its losses against the day whose
        # limits approved the trades (see RiskGate.report_loss).
        cycle_day_key = self.risk_gate.current_day_key(self.agent_id)

        # Resolve the curator profile for this cycle ONCE, before any market
        # data is fetched — its knobs (sizing multiplier, confidence floor,
        # enabled signals) are stable for the whole cycle.
        resolved_profile = self._resolve_curator_profile()
        if resolved_profile:
            result.curator = {
                "profile": self.curator.state.current_profile if self.curator else None,
                "knobs": resolved_profile,
            }

        logger.info(f"Starting trading cycle {cycle_id} for {len(assets)} assets")

        # --- Phase 1: Market Data --- (parallel across assets)
        market_data_tasks = [self._fetch_market_data(asset) for asset in assets]
        market_data_list = await asyncio.gather(*market_data_tasks, return_exceptions=True)

        # --- Phases 1.5-5: per-asset pipeline --- (parallel across assets)
        # Each asset's full path (integrity -> signal -> risk -> onchain log ->
        # execute) runs as its own coroutine. Blocking network calls (onchain
        # writes) run in worker threads via asyncio.to_thread so they overlap;
        # the shared risk gate and audit log are guarded by locks. Results are
        # collected per asset and merged back in input order so callers that
        # depend on ordering (tests, downstream consumers) see a stable shape.
        tasks = []
        errored_assets = []
        for i, md in enumerate(market_data_list):
            if isinstance(md, BaseException):
                result.errors.append(f"Market data error for {assets[i]}: {md}")
                logger.error(f"Market data error: {md}")
                errored_assets.append(assets[i])
                continue
            tasks.append(self._process_asset(md, cycle_id, result))

        per_asset = await asyncio.gather(*tasks, return_exceptions=True)
        for out in per_asset:
            if isinstance(out, BaseException):
                result.errors.append(f"Asset processing error: {out}")
                logger.error(f"Asset processing error: {out}")
                continue
            result.signals.extend(out["signals"])
            result.decisions.extend(out["decisions"])
            result.executions.extend(out["executions"])
            result.errors.extend(out["errors"])

        await self._report_cycle_loss(cycle_day_key)

        # S2/S3: classify the cycle outcome and publish the heartbeat.
        # Distinct signals, never conflated: "ran, no trades" (no_trades)
        # is not "rejected by risk gate" (rejected) is not "traded".
        from .metrics import (
            OUTCOME_ERROR,
            OUTCOME_NO_TRADES,
            OUTCOME_REJECTED,
            OUTCOME_TRADED,
            beat as _metrics_beat,
            inc as _metrics_inc,
        )
        n_decisions = len(result.decisions)
        n_executions = len(result.executions)
        if result.errors and n_decisions == 0 and n_executions == 0:
            outcome = OUTCOME_ERROR
        elif n_executions > 0:
            outcome = OUTCOME_TRADED
        elif n_decisions > 0:
            outcome = OUTCOME_REJECTED
        else:
            outcome = OUTCOME_NO_TRADES
        for d in result.decisions:
            sig = str(d.get("signal", "UNKNOWN"))
            if sig not in ("LONG", "SHORT", "NEUTRAL"):
                sig = "UNKNOWN"
            _metrics_inc("tars_decisions_total", {"direction": sig})
        for e in result.executions:
            _metrics_inc("tars_orders_total",
                         {"status": str(e.get("status", "unknown"))})
        _metrics_beat(
            cycle_id,
            outcome,
            counts={
                "signals": len(result.signals),
                "decisions": n_decisions,
                "executions": n_executions,
            },
            errors=len(result.errors),
        )
        return result

    async def _report_cycle_loss(self, cycle_day_key: str) -> None:
        """Feed the daily-loss limit from real account movement.

        Equity-delta model: compare OKX total equity against the previous
        live cycle's reading; a negative delta is reported to the risk gate
        as a realized loss pinned to this cycle's UTC day. This is what makes
        max_daily_loss_usd (and its auto-kill-switch) actually bite — before
        this, nothing in production ever called report_loss.

        Disabled in dry-run: there is no real account to read, and simulated
        deltas would trip a kill switch that protects nothing.
        """
        if self.dry_run:
            return
        try:
            balance = await read_okx_balance(self.cli)
        except Exception as e:  # noqa: BLE001 — a failed read must never kill the cycle
            logger.warning(f"Equity-delta loss feed: balance read failed: {e}")
            return
        equity = balance.get("total_eq") if isinstance(balance, dict) else None
        if not isinstance(equity, (int, float)) or equity <= 0:
            logger.warning("Equity-delta loss feed: no usable total_eq in balance snapshot")
            return
        equity = float(equity)
        # The read-modify-write of _last_cycle_equity AND the loss report run
        # in one critical section: two concurrent /trade cycles interleaving
        # here would each see a different prev and under/over-report the
        # day's loss (TOCTOU on the baseline). Only the network read stays
        # outside the lock so the critical section is pure memory work.
        async with self._risk_lock:
            prev = self._last_cycle_equity
            self._last_cycle_equity = equity
            if prev is not None:
                delta = equity - prev
                if delta < 0:
                    self.risk_gate.report_loss(self.agent_id, -delta, day_key=cycle_day_key)
                    reported = True
                else:
                    reported = False
            else:
                delta = 0.0
                reported = False
        if prev is None:
            logger.info(f"Equity-delta loss feed: baseline set at ${equity:.2f}")
        elif reported:
            logger.warning(
                f"Equity fell ${-delta:.2f} since last cycle — reported to risk gate "
                f"(pinned to {cycle_day_key})"
            )

    async def _process_asset(self, md: dict, cycle_id: str, result: "TradingCycleResult") -> dict:  # noqa: F821
        """Run the full per-asset pipeline for one market-data snapshot.

        Returns the lists this asset produced (signals/decisions/executions/
        errors) so the parent cycle can merge them in input order. Shared
        mutable state (risk gate, audit log) is guarded; blocking network I/O
        runs in worker threads.
        """
        asset = md["asset"]
        out: dict = {"signals": [], "decisions": [], "executions": [], "errors": []}

        # --- Phase 1.5: Pre-Signal Integrity Gate ---
        integrity = self._check_integrity(asset, md)
        if integrity and integrity.blocks_trading:
            msg = f"Integrity gate blocked {asset}: {integrity.reasons}"
            out["errors"].append(msg)
            logger.warning(f"Integrity gate blocked: {msg}")
            if self.audit_log:
                async with self._audit_lock:
                    self.audit_log.write("integrity_block", {
                        "cycle_id": cycle_id,
                        "asset": asset,
                        "reasons": integrity.reasons,
                    })
            return out

        # --- Phase 2: Signal Generation ---
        # The delta-neutral funding-arb package takes priority over the
        # directional signal path: a perp whose funding rate clears
        # `funding_arb_min_rate` runs the long-spot/short-perp package
        # INSTEAD of a directional bet this cycle (one trade per asset per
        # cycle). Directional signals still fire only when no arb package is
        # available for the asset.

        # Fetch spot price for funding carry signals (perp -> spot)
        spot_price = None
        perp_price = None
        perp_prices = self._extract_prices(md)
        if perp_prices:
            perp_price = perp_prices[-1]
        if asset.endswith("-SWAP"):
            spot_inst = asset.replace("-SWAP", "")
            try:
                spot_md = await self._fetch_market_data(spot_inst)
                spot_prices = self._extract_prices(spot_md)
                spot_price = spot_prices[-1] if spot_prices else None
            except Exception:
                pass

        # Update rolling ML history for 7-day rolling features
        if self.use_ml_carry_gate:
            funding_rate = float(md.get("funding_rate", 0.0))
            self._update_ml_history(asset, funding_rate, perp_price)

        if self._funding_arb_opportunity(asset, md, spot_price, perp_price):
            return await self._run_funding_arb_package(asset, md, cycle_id, out)

        signals = self._generate_signals(asset, md, spot_price)
        ensemble = ensemble_signal(asset, signals)

        out["signals"].append({
            "asset": asset,
            "ensemble": ensemble.to_dict(),
            "individual": [s.to_dict() for s in signals],
        })

        logger.info(
            f"Signal for {asset}: {ensemble.direction} "
            f"(conf: {ensemble.confidence_bps/100:.0f}%, "
            f"strategy: {ensemble.rationale[:80]}...)"
        )

        # Curator confidence floor
        if self._confidence_floor_bps is not None and ensemble.confidence_bps < self._confidence_floor_bps:
            msg = (f"Curator confidence floor {self._confidence_floor_bps}bps not met for "
                   f"{asset} ({ensemble.confidence_bps}bps)")
            logger.info(msg)
            if self.audit_log:
                async with self._audit_lock:
                    self.audit_log.write("curator_confidence_floor", {
                        "cycle_id": cycle_id,
                        "asset": asset,
                        "confidence_bps": ensemble.confidence_bps,
                        "floor_bps": self._confidence_floor_bps,
                    })
            return out

        # --- Phase 3: Risk Gate ---
        order = self._signal_to_order(ensemble, md)
        if order is None:
            logger.info(f"No order for {asset} (no tradeable signal or already positioned)")
            return out

        # I8 graded read: advisory only, logged + gauged, never gating.
        # Funding-arb packages are deliberately unscored — two-leg economics
        # don't fit a single-order score, and a misleading number is worse
        # than none.
        try:
            risk_score = self.risk_gate.score_order(order, self.agent_id)
            from .metrics import set_gauge as _metrics_gauge
            _metrics_gauge("tars_risk_score", risk_score.score)
            logger.info(
                f"Risk score for {asset}: {risk_score.score:.2f} "
                f"({risk_score.recommendation}) — "
                f"{'; '.join(risk_score.reasons) or 'no concerns'}"
            )
        except Exception as e:  # noqa: BLE001 — scoring must never block trading
            logger.warning(f"Risk scoring failed for {asset}: {e}")

        asset_prices = self._extract_prices(md)
        current_price = asset_prices[-1] if asset_prices else None

        async with self._risk_lock:
            if current_price is not None:
                self.risk_gate.observe_price(asset, current_price)
            current_position_side = (md.get("position") or {}).get("side")
            risk_result = self.risk_gate.check_order(
                order,
                self.agent_id,
                current_price=current_price,
                current_price_timestamp=md.get("timestamp"),
                current_position_side=current_position_side,
                # Pre-flight only — the executor re-checks and counts the order
                # at submission (place_order). A pre-flight count here would
                # charge the agent's own daily quota for an order that may
                # never execute (onchain log failure, rejected submission).
                count_trade=False,
            )
        if not risk_result.approved:
            out["errors"].append(f"Risk gate rejected {asset}: {risk_result.reason}")
            logger.warning(f"Risk gate rejected: {risk_result.code}: {risk_result.reason}")
            if self.audit_log:
                async with self._audit_lock:
                    self.audit_log.write("risk_rejection", {
                        "cycle_id": cycle_id,
                        "asset": asset,
                        "code": risk_result.code,
                        "reason": risk_result.reason,
                        "confidence_bps": ensemble.confidence_bps,
                    })
            return out

        # --- Phase 4: Onchain Decision Log ---
        # Strategy label: the ensemble's name plus every member strategy that
        # actually contributed, deterministically ordered — this string is
        # written to the immutable onchain decision record, so it must be
        # well-formed, not a mangled comprehension.
        member_strategies = [
            s.get("strategy", "unknown")
            for s in (ensemble.metadata.get("signals") or [])
        ]
        strategy_label = (
            f"{ensemble.strategy}+{'|'.join(member_strategies)}"
            if member_strategies
            else f"{ensemble.strategy}+no_members"
        )
        decision_payload = DecisionPayload(
            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
            agent_address=self.onchain_logger.agent_address if self.onchain_logger else "0x0000000000000000000000000000000000000000",
            asset=asset,
            signal=ensemble.direction,
            strategy=strategy_label,
            confidence_bps=ensemble.confidence_bps,
            entry_price=ensemble.entry_price or 0,
            size_usd=float(order.size),
            risk_params_hash=self.risk_gate.compute_risk_hash() if hasattr(self.risk_gate, 'compute_risk_hash') else "0x" + "00" * 32,
            timestamp=int(time.time()),
        )

        log_tx = None
        if self.onchain_logger and not self.dry_run:
            try:
                log_tx = await asyncio.to_thread(self.onchain_logger.log_decision, decision_payload)
                out["decisions"].append({
                    "decision_id": decision_payload.decision_id,
                    "asset": asset,
                    "tx_hash": log_tx,
                    "signal": ensemble.direction,
                    "confidence_bps": ensemble.confidence_bps,
                    "confidence": ensemble.confidence_bps / 10000.0,
                    "side": order.side,
                    "size_usd": float(order.size),
                    "risk_hash": decision_payload.risk_params_hash,
                    "status": "approved",
                })
                logger.info(f"Decision logged onchain: {log_tx}")
            except Exception as e:
                out["errors"].append(f"Onchain log failed for {asset}: {e}")
                logger.error(f"Onchain logging failed: {e}")
                # In production: do NOT execute if onchain log fails
                return out
        else:
            out["decisions"].append({
                "decision_id": decision_payload.decision_id,
                "asset": asset,
                "tx_hash": None,
                "signal": ensemble.direction,
                "confidence_bps": ensemble.confidence_bps,
                "confidence": ensemble.confidence_bps / 10000.0,
                "side": order.side,
                "size_usd": float(order.size),
                "risk_hash": decision_payload.risk_params_hash,
                "status": "approved",
                "dry_run": True,
            })

        # --- Phase 5: Execution ---
        try:
            order_result = await self.executor.place_order(
                order,
                current_price=current_price,
                current_price_timestamp=md.get("timestamp"),
            )
            fill_ok = order_result.fill_verified is not False and (
                order_result.fill_verified is True or self.dry_run
            )
            if order_result.state in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED) and fill_ok:
                execution_status = "success"
            elif order_result.fill_verified is False:
                execution_status = "fill_verification_failed"
            elif (
                order_result.state in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)
                and order_result.fill_verified is None and not self.dry_run
            ):
                # A LIVE fill we could not verify against a reference price:
                # never silently reported as success — the decision record
                # says exactly why the fill is untrusted.
                execution_status = "fill_verification_unverified"
                logger.warning(
                    "FILL_UNVERIFIED: order %s (%s) filled but no reference "
                    "price was available to verify slippage against.",
                    order_result.order_id, asset,
                )
            else:
                execution_status = order_result.state.value
            out["executions"].append({
                "decision_id": decision_payload.decision_id,
                "asset": asset,
                "order_id": order_result.order_id,
                "client_oid": order_result.client_oid,
                "state": order_result.state.value,
                "fill_px": order_result.fill_px,
                "fill_price": order_result.fill_px,
                "slippage_pct": order_result.slippage_pct,
                "size_usd": float(order.size),
                "tx_hash": None,
                "status": execution_status,
                "fee": order_result.fee,
                "fee_ccy": order_result.fee_ccy,
            })
            logger.info(f"Order placed: {order_result.order_id}, state={order_result.state}")

            if log_tx and self.onchain_logger:
                await asyncio.to_thread(
                    self.onchain_logger.record_execution,
                    decision_id=decision_payload.decision_id,
                    fill_price=float(order_result.fill_px or 0),
                    fill_size_usd=float(order.size),
                    fee_usd=float(order_result.fee or 0),
                    success=fill_ok,
                )

            if order_result.state in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
                async with self._risk_lock:
                    self.risk_gate.report_volume(self.agent_id, float(order.size))

        except ExecutionError as e:
            out["errors"].append(f"Execution failed for {asset}: {e}")
            logger.error(f"Execution error: {e}")
            if self.onchain_logger and log_tx:
                await asyncio.to_thread(
                    self.onchain_logger.record_execution,
                    decision_id=decision_payload.decision_id,
                    fill_price=0,
                    fill_size_usd=0,
                    fee_usd=0,
                    success=False,
                )

        return out

    async def _process_asset_consensus(self, asset: str, md: dict, cycle_id: str, out: dict) -> dict:
        """Process asset through Product B consensus gate (swarm trading)."""
        
        # Build market context for traders
        prices = self._extract_prices(md)
        price_data = self._extract_price_data(md)
        funding_rate = md.get("funding_rate", 0.0)
        
        # Get spot/perp prices for funding carry trader
        spot_price = None
        perp_price = None
        if asset.endswith("-SWAP"):
            # Need to fetch spot price
            spot_asset = asset.replace("-SWAP", "")
            try:
                spot_md = await self._fetch_market_data(spot_asset)
                spot_prices = self._extract_prices(spot_md)
                spot_price = spot_prices[-1] if spot_prices else None
                perp_price = prices[-1] if prices else None
            except Exception:
                pass
        
        context = MarketContext(
            asset=asset,
            prices=prices,
            price_data=price_data,
            funding_rate=funding_rate,
            spot_price=spot_price,
            perp_price=perp_price,
            next_funding_ts=None,  # Would need to fetch from OKX
            whale_net_flow_usd=0.0,
            exchange_reserve_change_pct=0.0,
            stablecoin_supply_change_pct=0.0,
            metadata={
                "price_data_by_tf": md.get("price_data_by_tf"),
            },
        )
        
        # Determine cohort (default to majors for now)
        cohort = "majors"
        
        # Run consensus
        consensus: ConsensusResult = self.consensus_gate.compute_consensus(
            asset=asset,
            cohort=cohort,
            traders=self._traders_by_cohort.get(cohort, []),
            context=context,
        )
        
        # Log individual trader signals
        for vote in consensus.votes:
            out["signals"].append({
                "asset": asset,
                "trader": vote.trader_name,
                "signal": vote.signal.to_dict(),
                "vote_weight": vote.vote_weight,
            })
        
        logger.info(
            f"Consensus for {asset} [{cohort}]: {consensus.direction} "
            f"(conf: {consensus.consensus_confidence_bps/100:.0f}%, "
            f"threshold={consensus.threshold:.0%}, reached={consensus.consensus_reached})"
        )
        logger.debug(f"Consensus rationale: {consensus.rationale}")
        
        if self.audit_log:
            async with self._audit_lock:
                self.audit_log.write("consensus_result", {
                    "cycle_id": cycle_id,
                    "asset": asset,
                    "cohort": cohort,
                    "consensus_reached": consensus.consensus_reached,
                    "direction": consensus.direction,
                    "confidence_bps": consensus.consensus_confidence_bps,
                    "threshold": consensus.threshold,
                    "long_weight": consensus.long_weight,
                    "short_weight": consensus.short_weight,
                    "total_weight": consensus.total_weight,
                    "votes": [
                        {
                            "trader": v.trader_name,
                            "direction": v.signal.direction,
                            "confidence_bps": v.signal.confidence_bps,
                            "weight": v.weight,
                        }
                        for v in consensus.votes
                    ],
                })
        
        # Handle no consensus
        if not consensus.consensus_reached:
            if consensus.behavior == ConsensusBehavior.DEFAULT_TRADER and consensus.default_signal:
                # Use default trader's signal
                logger.info(f"Using default trader signal for {asset}")
                consensus.direction = consensus.default_signal.direction
                consensus.consensus_confidence_bps = consensus.default_signal.confidence_bps
            elif consensus.behavior == ConsensusBehavior.REDUCED_SIZE:
                logger.info(f"Consensus not reached for {asset}, would trade at reduced size (not implemented)")
                return out
            else:  # SKIP or HOLD_AND_LOG
                logger.info(f"Consensus not reached for {asset}: {consensus.behavior.value}")
                return out
        
        # Curator confidence floor
        if self._confidence_floor_bps is not None and consensus.consensus_confidence_bps < self._confidence_floor_bps:
            msg = (f"Curator confidence floor {self._confidence_floor_bps}bps not met for "
                   f"{asset} ({consensus.consensus_confidence_bps}bps)")
            logger.info(msg)
            if self.audit_log:
                async with self._audit_lock:
                    self.audit_log.write("curator_confidence_floor", {
                        "cycle_id": cycle_id,
                        "asset": asset,
                        "confidence_bps": consensus.consensus_confidence_bps,
                        "floor_bps": self._confidence_floor_bps,
                    })
            return out
        
        # --- Phase 3: Risk Gate ---
        # Create order from consensus
        order_size = self.max_position_usd * (consensus.consensus_confidence_bps / 10000.0) * self._position_size_multiplier
        if order_size <= 0:
            logger.info(f"No order for {asset} (consensus confidence too low)")
            return out
        
        side_map: dict[str, Literal["buy", "sell"]] = {"LONG": "buy", "SHORT": "sell"}
        side = side_map.get(consensus.direction)
        if side is None:
            logger.info(f"No order for {asset} (direction {consensus.direction})")
            return out
        order = OrderRequest(
            inst_id=asset,
            side=side,
            order_type="market",
            size=f"{order_size:.2f}",
            client_oid=f"consensus_{asset}_{uuid.uuid4().hex[:8]}",
            reduce_only=False,
            confidence_bps=consensus.consensus_confidence_bps,
        )
        
        asset_prices = self._extract_prices(md)
        current_price = asset_prices[-1] if asset_prices else None
        
        async with self._risk_lock:
            if current_price is not None:
                self.risk_gate.observe_price(asset, current_price)
            current_position_side = (md.get("position") or {}).get("side")
            risk_result = self.risk_gate.check_order(
                order,
                self.agent_id,
                current_price=current_price,
                current_price_timestamp=md.get("timestamp"),
                current_position_side=current_position_side,
                count_trade=False,
            )
        if not risk_result.approved:
            out["errors"].append(f"Risk gate rejected {asset}: {risk_result.reason}")
            logger.warning(f"Risk gate rejected: {risk_result.code}: {risk_result.reason}")
            if self.audit_log:
                async with self._audit_lock:
                    self.audit_log.write("risk_rejection", {
                        "cycle_id": cycle_id,
                        "asset": asset,
                        "code": risk_result.code,
                        "reason": risk_result.reason,
                        "confidence_bps": consensus.consensus_confidence_bps,
                    })
            return out
        
        # --- Phase 4: Onchain Decision Log ---
        # Build strategy label from contributing traders
        contributing = [
            v.trader_name for v in consensus.votes
            if v.signal.direction == consensus.direction and v.signal.is_tradeable
        ]
        strategy_label = f"consensus_{cohort}+{'|'.join(contributing)}"
        
        decision_payload = DecisionPayload(
            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
            agent_address=self.onchain_logger.agent_address if self.onchain_logger else "0x0000000000000000000000000000000000000000",
            asset=asset,
            signal=consensus.direction,
            strategy=strategy_label,
            confidence_bps=consensus.consensus_confidence_bps,
            entry_price=current_price or 0,
            size_usd=float(order.size),
            risk_params_hash=self.risk_gate.compute_risk_hash() if hasattr(self.risk_gate, 'compute_risk_hash') else "0x" + "00" * 32,
            timestamp=int(time.time()),
        )
        
        log_tx = None
        if self.onchain_logger and not self.dry_run:
            try:
                log_tx = await asyncio.to_thread(self.onchain_logger.log_decision, decision_payload)
                out["decisions"].append({
                    "decision_id": decision_payload.decision_id,
                    "asset": asset,
                    "tx_hash": log_tx,
                    "signal": consensus.direction,
                    "confidence_bps": consensus.consensus_confidence_bps,
                    "confidence": consensus.consensus_confidence_bps / 10000.0,
                    "side": order.side,
                    "size_usd": float(order.size),
                    "risk_hash": decision_payload.risk_params_hash,
                    "status": "approved",
                })
                logger.info(f"Decision logged onchain: {log_tx}")
            except Exception as e:
                out["errors"].append(f"Onchain log failed for {asset}: {e}")
                logger.error(f"Onchain logging failed: {e}")
                return out
        else:
            out["decisions"].append({
                "decision_id": decision_payload.decision_id,
                "asset": asset,
                "tx_hash": None,
                "signal": consensus.direction,
                "confidence_bps": consensus.consensus_confidence_bps,
                "confidence": consensus.consensus_confidence_bps / 10000.0,
                "side": order.side,
                "size_usd": float(order.size),
                "risk_hash": decision_payload.risk_params_hash,
                "status": "approved",
                "dry_run": True,
            })
        
        # --- Phase 5: Execution ---
        try:
            order_result = await self.executor.place_order(
                order,
                current_price=current_price,
                current_price_timestamp=md.get("timestamp"),
            )
            fill_ok = order_result.fill_verified is not False and (
                order_result.fill_verified is True or self.dry_run
            )
            if order_result.state in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED) and fill_ok:
                execution_status = "success"
            elif order_result.fill_verified is False:
                execution_status = "fill_verification_failed"
            elif (
                order_result.state in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)
                and order_result.fill_verified is None and not self.dry_run
            ):
                execution_status = "fill_verification_unverified"
                logger.warning(
                    "FILL_UNVERIFIED: order %s (%s) filled but no reference "
                    "price was available to verify slippage against.",
                    order_result.order_id, asset,
                )
            else:
                execution_status = order_result.state.value
            out["executions"].append({
                "decision_id": decision_payload.decision_id,
                "asset": asset,
                "order_id": order_result.order_id,
                "client_oid": order_result.client_oid,
                "state": order_result.state.value,
                "fill_px": order_result.fill_px,
                "fill_price": order_result.fill_px,
                "slippage_pct": order_result.slippage_pct,
                "size_usd": float(order.size),
                "tx_hash": None,
                "status": execution_status,
                "fee": order_result.fee,
                "fee_ccy": order_result.fee_ccy,
            })
            logger.info(f"Order placed: {order_result.order_id}, state={order_result.state}")
            
            if log_tx and self.onchain_logger:
                await asyncio.to_thread(
                    self.onchain_logger.record_execution,
                    decision_id=decision_payload.decision_id,
                    fill_price=float(order_result.fill_px or 0),
                    fill_size_usd=float(order.size),
                    fee_usd=float(order_result.fee or 0),
                    success=fill_ok,
                )
            
            if order_result.state in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
                async with self._risk_lock:
                    self.risk_gate.report_volume(self.agent_id, float(order.size))
        
        except ExecutionError as e:
            out["errors"].append(f"Execution failed for {asset}: {e}")
            logger.error(f"Execution error: {e}")
            if self.onchain_logger and log_tx:
                await asyncio.to_thread(
                    self.onchain_logger.record_execution,
                    decision_id=decision_payload.decision_id,
                    fill_price=0,
                    fill_size_usd=0,
                    fee_usd=0,
                    success=False,
                )
        
        return out
