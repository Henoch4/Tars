"""
Trader abstraction layer — Product B (Swarm Trading).

Each trader is an independently pluggable class with its own hyperparameters,
following the algo-backtester pattern: Strategy base class with named kwargs
and defaults, on_data(prices, indicators, portfolio) interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from .signals import Signal, SignalDirection


@dataclass
class MarketContext:
    """Market data passed to each trader's on_data method."""
    asset: str
    prices: list[float]                    # Close prices (1h bars)
    price_data: list[dict]                 # [{"close": float, "volume": float, "ts": int}, ...]
    funding_rate: float = 0.0
    spot_price: float | None = None
    perp_price: float | None = None
    next_funding_ts: int | None = None
    whale_net_flow_usd: float = 0.0
    exchange_reserve_change_pct: float = 0.0
    stablecoin_supply_change_pct: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class TraderConfig:
    """Per-trader configuration (hyperparameters with defaults)."""
    name: str
    asset_class_cohort: str = "majors"     # "majors" | "alts" | "memecoins" | "tokenized_stocks"
    enabled: bool = True
    weight: float = 1.0                    # Vote weight in consensus
    # Hyperparameters specific to each trader subclass
    params: dict = field(default_factory=dict)


class TraderStrategy(ABC):
    """
    Base class for all trader agents. Follows algo-backtester pattern:
    - Named kwargs with defaults in __init__
    - on_data(context) -> Signal
    - Pluggable, independently configurable
    """
    
    def __init__(
        self,
        name: str,
        asset_class_cohort: str = "majors",
        weight: float = 1.0,
        **params
    ):
        self.name = name
        self.asset_class_cohort = asset_class_cohort
        self.weight = weight
        self.params = params
        self._config = TraderConfig(
            name=name,
            asset_class_cohort=asset_class_cohort,
            weight=weight,
            params=params
        )
    
    @property
    def config(self) -> TraderConfig:
        return self._config
    
    @abstractmethod
    def on_data(self, context: MarketContext) -> Signal:
        """
        Generate a trading signal from market context.
        
        Args:
            context: MarketContext with prices, indicators, funding, on-chain data
            
        Returns:
            Signal with direction, confidence_bps, rationale
        """
        pass
    
    def update_params(self, **params) -> None:
        """Hot-reload hyperparameters without recreating the trader."""
        self.params.update(params)
        self._config.params = self.params
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name}, cohort={self.asset_class_cohort}, weight={self.weight})"


# ─── Concrete Trader Implementations ───

class MeanReversionTrader(TraderStrategy):
    """Z-score based mean reversion with structural trend guard."""
    
    def __init__(
        self,
        name: str = "mean_reversion",
        asset_class_cohort: str = "majors",
        weight: float = 1.0,
        window: int = 20,
        z_threshold: float = 2.0,
        regime_window: int = 50,
        min_confidence_bps: int = 6000,
    ):
        super().__init__(
            name=name,
            asset_class_cohort=asset_class_cohort,
            weight=weight,
            window=window,
            z_threshold=z_threshold,
            regime_window=regime_window,
            min_confidence_bps=min_confidence_bps,
        )
        self.window = window
        self.z_threshold = z_threshold
        self.regime_window = regime_window
        self.min_confidence_bps = min_confidence_bps
    
    def on_data(self, context: MarketContext) -> Signal:
        from .signals import mean_reversion_signal
        return mean_reversion_signal(
            asset=context.asset,
            prices=context.prices,
            window=self.window,
            z_threshold=self.z_threshold,
            regime_window=self.regime_window,
        )


class MomentumTrader(TraderStrategy):
    """MA crossover momentum with volume confirmation and regime guard."""
    
    def __init__(
        self,
        name: str = "momentum",
        asset_class_cohort: str = "majors",
        weight: float = 1.0,
        short_window: int = 5,
        long_window: int = 20,
        regime_window: int = 50,
        min_volume_ratio: float = 0.8,
    ):
        super().__init__(
            name=name,
            asset_class_cohort=asset_class_cohort,
            weight=weight,
            short_window=short_window,
            long_window=long_window,
            regime_window=regime_window,
            min_volume_ratio=min_volume_ratio,
        )
        self.short_window = short_window
        self.long_window = long_window
        self.regime_window = regime_window
        self.min_volume_ratio = min_volume_ratio
    
    def on_data(self, context: MarketContext) -> Signal:
        from .signals import momentum_signal
        return momentum_signal(
            asset=context.asset,
            price_data=context.price_data,
            short_window=self.short_window,
            long_window=self.long_window,
            regime_window=self.regime_window,
        )


class FundingRateTrader(TraderStrategy):
    """Contrarian funding rate signal (directional, NOT hedged)."""
    
    def __init__(
        self,
        name: str = "funding_rate",
        asset_class_cohort: str = "majors",
        weight: float = 1.0,
        threshold: float = 0.001,
    ):
        super().__init__(
            name=name,
            asset_class_cohort=asset_class_cohort,
            weight=weight,
            threshold=threshold,
        )
        self.threshold = threshold
    
    def on_data(self, context: MarketContext) -> Signal:
        from .signals import funding_rate_signal
        return funding_rate_signal(
            asset=context.asset,
            funding_rate=context.funding_rate,
            threshold=self.threshold,
        )


class FundingCarryTrader(TraderStrategy):
    """Delta-neutral funding carry (long spot + short perp)."""
    
    def __init__(
        self,
        name: str = "funding_carry",
        asset_class_cohort: str = "majors",
        weight: float = 1.5,  # Higher weight for true arb
        min_basis_bps: float = 5.0,
        min_annualized_apr: float = 10.0,
    ):
        super().__init__(
            name=name,
            asset_class_cohort=asset_class_cohort,
            weight=weight,
            min_basis_bps=min_basis_bps,
            min_annualized_apr=min_annualized_apr,
        )
        self.min_basis_bps = min_basis_bps
        self.min_annualized_apr = min_annualized_apr
    
    def on_data(self, context: MarketContext) -> Signal:
        if context.spot_price is None or context.perp_price is None:
            from .signals import Signal
            return Signal(
                strategy="funding_carry",
                asset=context.asset,
                direction="NEUTRAL",
                confidence_bps=0,
                rationale="Missing spot or perp price for carry calculation",
            )
        from .signals import funding_carry_signal
        return funding_carry_signal(
            asset=context.asset,
            spot_price=context.spot_price,
            perp_price=context.perp_price,
            funding_rate=context.funding_rate,
            next_funding_ts=context.next_funding_ts or 0,
            min_basis_bps=self.min_basis_bps,
            min_annualized_apr=self.min_annualized_apr,
        )


class MultiTimeframeTrader(TraderStrategy):
    """Multi-timeframe momentum with HTF alignment filter."""
    
    def __init__(
        self,
        name: str = "multi_timeframe",
        asset_class_cohort: str = "majors",
        weight: float = 1.0,
        tf_weights: dict[str, float] | None = None,
    ):
        super().__init__(
            name=name,
            asset_class_cohort=asset_class_cohort,
            weight=weight,
            tf_weights=tf_weights or {},
        )
        self.tf_weights = tf_weights or {
            "15m": 0.15, "1H": 0.25, "4H": 0.30, "1D": 0.30
        }
    
    def on_data(self, context: MarketContext) -> Signal:
        # Requires multi-timeframe price data in context.metadata
        tf_data = context.metadata.get("price_data_by_tf")
        if not tf_data:
            from .signals import Signal
            return Signal(
                strategy="multi_timeframe",
                asset=context.asset,
                direction="NEUTRAL",
                confidence_bps=0,
                rationale="No multi-timeframe data in context",
            )
        from .signals import multi_timeframe_signal
        return multi_timeframe_signal(
            asset=context.asset,
            price_data_by_tf=tf_data,
            tf_weights=self.tf_weights,
        )


class VolumeWeightedTrader(TraderStrategy):
    """VWAP deviation + volume trend confirmation."""
    
    def __init__(
        self,
        name: str = "volume_weighted",
        asset_class_cohort: str = "majors",
        weight: float = 1.0,
        window: int = 20,
        deviation_threshold: float = 1.0,
        vol_trend_threshold: float = 1.1,
    ):
        super().__init__(
            name=name,
            asset_class_cohort=asset_class_cohort,
            weight=weight,
            window=window,
            deviation_threshold=deviation_threshold,
            vol_trend_threshold=vol_trend_threshold,
        )
        self.window = window
        self.deviation_threshold = deviation_threshold
        self.vol_trend_threshold = vol_trend_threshold
    
    def on_data(self, context: MarketContext) -> Signal:
        from .signals import volume_weighted_signal
        return volume_weighted_signal(
            asset=context.asset,
            price_data=context.price_data,
            window=self.window,
        )


class OnChainFlowTrader(TraderStrategy):
    """Whale/exchange reserve/stablecoin flow signals."""
    
    def __init__(
        self,
        name: str = "onchain_flow",
        asset_class_cohort: str = "majors",
        weight: float = 1.0,
        min_whale_flow_usd: float = 10_000_000,
    ):
        super().__init__(
            name=name,
            asset_class_cohort=asset_class_cohort,
            weight=weight,
            min_whale_flow_usd=min_whale_flow_usd,
        )
        self.min_whale_flow_usd = min_whale_flow_usd
    
    def on_data(self, context: MarketContext) -> Signal:
        from .signals import onchain_flow_signal
        return onchain_flow_signal(
            asset=context.asset,
            whale_net_flow_usd=context.whale_net_flow_usd,
            exchange_reserve_change_pct=context.exchange_reserve_change_pct,
            stablecoin_supply_change_pct=context.stablecoin_supply_change_pct,
            min_whale_flow_usd=self.min_whale_flow_usd,
        )


# ─── Trader Registry ───

DEFAULT_TRADERS: dict[str, type[TraderStrategy]] = {
    "mean_reversion": MeanReversionTrader,
    "momentum": MomentumTrader,
    "funding_rate": FundingRateTrader,
    "funding_carry": FundingCarryTrader,
    "multi_timeframe": MultiTimeframeTrader,
    "volume_weighted": VolumeWeightedTrader,
    "onchain_flow": OnChainFlowTrader,
}


def create_trader(name: str, **params) -> TraderStrategy:
    """Factory to create trader instances by name."""
    if name not in DEFAULT_TRADERS:
        raise ValueError(f"Unknown trader: {name}. Available: {list(DEFAULT_TRADERS.keys())}")
    return DEFAULT_TRADERS[name](**params)


def create_default_swarm() -> list[TraderStrategy]:
    """Create the default swarm of traders for majors cohort."""
    return [
        MeanReversionTrader(),
        MomentumTrader(),
        FundingRateTrader(),
        FundingCarryTrader(),  # True delta-neutral arb
    ]