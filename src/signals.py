"""
Trading signal engine for the autonomous trading agent.

Implements multiple signal strategies with confidence scoring:
  - Mean reversion (Z-score based on rolling window)
  - Momentum (price trend + volume confirmation)
  - Smart-money flow divergence
  - Funding-rate arbitrage signal
  - Multi-timeframe confirmation
  - Volume-weighted price action
  - Funding carry basis
  - On-chain flow signals (whale, exchange reserves)

Each signal returns a Signal object with direction, confidence, and rationale.
The risk engine then gates these before any execution.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional


SignalDirection = Literal["LONG", "SHORT", "NEUTRAL"]


class SignalStrength(Enum):
    WEAK = 0.3
    MODERATE = 0.5
    STRONG = 0.7
    VERY_STRONG = 0.9


@dataclass
class Signal:
    """A single trading signal from one strategy."""
    strategy: str
    asset: str          # e.g. "BTC-USDT-SWAP"
    direction: SignalDirection
    confidence_bps: int   # 0–10000 (basis points, 7000 = 70%)
    entry_price: float | None = None
    rationale: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def is_tradeable(self) -> bool:
        """A signal is tradeable if direction != NEUTRAL and confidence >= 60%."""
        return self.direction != "NEUTRAL" and self.confidence_bps >= 6000

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "asset": self.asset,
            "direction": self.direction,
            "confidence_bps": self.confidence_bps,
            "entry_price": self.entry_price,
            "rationale": self.rationale,
            "metadata": self.metadata,
            "is_tradeable": self.is_tradeable,
        }


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --- Regime filter (roadmap Phase 2) ---

# Strategies in this family derive their evidence from the SAME price series.
# Agreement between them is correlated evidence, not independent confirmation
# — the ensemble applies a haircut instead of counting them twice.
PRICE_ACTION_STRATEGIES = frozenset({"mean_reversion", "momentum"})

# Correlated-evidence haircut: the second+ price-action signal agreeing with
# the family's best counts this fraction of its confidence.
CORRELATED_EVIDENCE_WEIGHT = 0.5


def trend_regime(prices: list[float], regime_window: int = 50) -> dict:
    """Classify the structural trend over a window LONGER than any signal's
    own lookback. Returns {"regime": "up"|"down"|"flat", "slope_pct": float}.

    Deliberately boring: net drift over the window, thresholded at ±2%. A
    linear fit would be smoother but no more honest at this data quality.
    With insufficient data returns regime="unknown" — callers must treat
    unknown as no-filter (fail-open for signal generation; the risk gate
    remains the fail-closed layer).
    """
    if len(prices) < regime_window:
        return {"regime": "unknown", "slope_pct": 0.0}
    window = prices[-regime_window:]
    first = _safe_float(window[0], 0.0)
    last = _safe_float(window[-1], 0.0)
    if first <= 0:
        return {"regime": "unknown", "slope_pct": 0.0}
    slope_pct = (last - first) / first * 100.0
    if slope_pct > 2.0:
        regime = "up"
    elif slope_pct < -2.0:
        regime = "down"
    else:
        regime = "flat"
    return {"regime": regime, "slope_pct": slope_pct}


def mean_reversion_signal(
    asset: str,
    prices: list[float],
    window: int = 20,
    z_threshold: float = 2.0,
    regime_window: int = 0,
) -> Signal:
    """
    Mean-reversion signal based on Z-score of recent prices.
    Buy when price is significantly below the rolling mean (oversold).
    Sell when significantly above (overbought).

    regime_window > 0 enables the structural-trend guard: an oversold LONG is
    suppressed in a sustained downtrend (and an overbought SHORT in a sustained
    uptrend) — "buying the dip" into structural decline is catching a knife,
    not mean reversion. Insufficient data = filter inactive.
    """
    if len(prices) < window + 2:
        return Signal(
            strategy="mean_reversion",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=0,
            entry_price=prices[-1] if prices else None,
            rationale=f"Insufficient data: {len(prices)} < {window + 2} required",
        )

    recent = prices[-window:]
    mean_price = statistics.mean(recent)
    std_price = statistics.pstdev(recent) if len(recent) > 1 else 0.0

    if std_price == 0 or math.isnan(std_price):
        return Signal(
            strategy="mean_reversion",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=0,
            entry_price=prices[-1],
            rationale="Zero or NaN standard deviation — no signal",
        )

    z_score = (prices[-1] - mean_price) / std_price
    current_price = prices[-1]

    # Structural-trend guard (only when enabled AND enough history exists).
    regime = (
        trend_regime(prices, regime_window)
        if regime_window > 0
        else {"regime": "unknown", "slope_pct": 0.0}
    )

    # Higher |z| → higher confidence
    confidence_factor = min(abs(z_score) / z_threshold, 1.0)
    # Base confidence: 60% minimum if we have a signal
    base_confidence = 0.60
    # Scale from 60% to 95% based on how extreme the z-score is
    confidence = base_confidence + (0.35 * confidence_factor)
    confidence_bps = int(confidence * 10000)

    if z_score < -z_threshold:
        if regime["regime"] == "down":
            # Oversold INTO a structural downtrend: the "cheap" price is cheap
            # for a trend reason, not a reversion reason. Stand down.
            return Signal(
                strategy="mean_reversion",
                asset=asset,
                direction="NEUTRAL",
                confidence_bps=0,
                entry_price=current_price,
                rationale=(
                    f"Z-score {z_score:.2f} below -{z_threshold} (oversold), but "
                    f"structural trend over {regime_window} bars is DOWN "
                    f"({regime['slope_pct']:.1f}%) — suppressed: buying this dip "
                    f"is knife-catching, not mean reversion."
                ),
                metadata={
                    "z_score": z_score,
                    "mean": mean_price,
                    "std": std_price,
                    "current_price": current_price,
                    "regime": regime,
                },
            )
        # Price is below mean → oversold → buy
        return Signal(
            strategy="mean_reversion",
            asset=asset,
            direction="LONG",
            confidence_bps=min(confidence_bps, 9500),
            entry_price=current_price,
            rationale=(
                f"Z-score {z_score:.2f} below -{z_threshold} threshold. "
                f"Price {current_price:.2f} vs mean {mean_price:.2f} "
                f"(std {std_price:.2f}). Oversold — mean reversion expected."
            ),
            metadata={
                "z_score": z_score,
                "mean": mean_price,
                "std": std_price,
                "current_price": current_price,
                "regime": regime,
            },
        )
    elif z_score > z_threshold:
        if regime["regime"] == "up":
            return Signal(
                strategy="mean_reversion",
                asset=asset,
                direction="NEUTRAL",
                confidence_bps=0,
                entry_price=current_price,
                rationale=(
                    f"Z-score {z_score:.2f} above +{z_threshold} (overbought), but "
                    f"structural trend over {regime_window} bars is UP "
                    f"({regime['slope_pct']:.1f}%) — suppressed: shorting strength "
                    f"in a sustained uptrend."
                ),
                metadata={
                    "z_score": z_score,
                    "mean": mean_price,
                    "std": std_price,
                    "current_price": current_price,
                    "regime": regime,
                },
            )
        # Price is above mean → overbought → sell/short
        return Signal(
            strategy="mean_reversion",
            asset=asset,
            direction="SHORT",
            confidence_bps=min(confidence_bps, 9500),
            entry_price=current_price,
            rationale=(
                f"Z-score {z_score:.2f} above +{z_threshold} threshold. "
                f"Price {current_price:.2f} vs mean {mean_price:.2f} "
                f"(std {std_price:.2f}). Overbought — mean reversion expected."
            ),
            metadata={
                "z_score": z_score,
                "mean": mean_price,
                "std": std_price,
                "current_price": current_price,
                "regime": regime,
            },
        )
    else:
        # Within mean — no signal, but note convergence
        strength = SignalStrength.WEAK.value
        return Signal(
            strategy="mean_reversion",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=int(strength * 10000),
            entry_price=current_price,
            rationale=(
                f"Z-score {z_score:.2f} within ±{z_threshold} band. "
                f"No mean-reversion opportunity."
            ),
            metadata={
                "z_score": z_score,
                "mean": mean_price,
                "std": std_price,
                "current_price": current_price,
            },
        )


def momentum_signal(
    asset: str,
    price_data: list[dict],
    short_window: int = 5,
    long_window: int = 20,
    regime_window: int = 0,
) -> Signal:
    """
    Momentum signal based on moving average crossover + volume confirmation.

    price_data items: {"close": float, "volume": float}

    regime_window > 0 adds a longer-horizon confirmation: a crossover LONG in
    a sustained structural downtrend (or SHORT in an uptrend) is more likely a
    pullback than a regime change — suppressed to NEUTRAL. Insufficient data
    = filter inactive.
    """
    if len(price_data) < long_window:
        return Signal(
            strategy="momentum",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=0,
            entry_price=price_data[-1]["close"] if price_data else None,
            rationale=f"Insufficient data: {len(price_data)} < {long_window}",
        )

    closes = [d["close"] for d in price_data]
    short_ma = statistics.mean(closes[-short_window:])
    long_ma = statistics.mean(closes[-long_window:])
    current_price = closes[-1]

    # Volume confirmation
    recent_volumes = [d["volume"] for d in price_data[-short_window:]]
    avg_volume = statistics.mean(recent_volumes) if recent_volumes else 0
    vol_ratio = recent_volumes[-1] / avg_volume if avg_volume > 0 else 1.0
    vol_confirmed = vol_ratio > 0.8  # at least 80% of recent avg volume

    # Price momentum
    price_change = (current_price - long_ma) / long_ma if long_ma > 0 else 0

    # Longer-horizon regime confirmation (only when enabled AND enough data).
    closes_all = [d["close"] for d in price_data]
    regime = (
        trend_regime(closes_all, regime_window)
        if regime_window > 0
        else {"regime": "unknown", "slope_pct": 0.0}
    )

    # Confidence based on MA spread + volume confirmation
    spread_ratio = abs(short_ma - long_ma) / long_ma if long_ma > 0 else 0
    confidence = min(spread_ratio * 50 + (0.6 if vol_confirmed else 0.4), 0.95)
    confidence_bps = int(confidence * 10000)

    if short_ma > long_ma * 1.001 and price_change > 0:
        if regime["regime"] == "down":
            return Signal(
                strategy="momentum",
                asset=asset,
                direction="NEUTRAL",
                confidence_bps=0,
                entry_price=current_price,
                rationale=(
                    f"MA crossover bullish (short {short_ma:.2f} > long {long_ma:.2f}), "
                    f"but structural trend over {regime_window} bars is DOWN "
                    f"({regime['slope_pct']:.1f}%) — suppressed: likely pullback, "
                    f"not regime change."
                ),
                metadata={
                    "short_ma": short_ma,
                    "long_ma": long_ma,
                    "vol_ratio": vol_ratio,
                    "price_change": price_change,
                    "regime": regime,
                },
            )
        return Signal(
            strategy="momentum",
            asset=asset,
            direction="LONG",
            confidence_bps=confidence_bps,
            entry_price=current_price,
            rationale=(
                f"MA crossover: short MA {short_ma:.2f} > long MA {long_ma:.2f}. "
                f"Volume {'confirmed' if vol_confirmed else 'weak'} "
                f"(ratio {vol_ratio:.2f}). Price up {price_change:.2%}."
            ),
            metadata={
                "short_ma": short_ma,
                "long_ma": long_ma,
                "vol_ratio": vol_ratio,
                "price_change": price_change,
                "regime": regime,
            },
        )
    elif short_ma < long_ma * 0.999 and price_change < 0:
        if regime["regime"] == "up":
            return Signal(
                strategy="momentum",
                asset=asset,
                direction="NEUTRAL",
                confidence_bps=0,
                entry_price=current_price,
                rationale=(
                    f"MA crossover bearish (short {short_ma:.2f} < long {long_ma:.2f}), "
                    f"but structural trend over {regime_window} bars is UP "
                    f"({regime['slope_pct']:.1f}%) — suppressed: likely pullback, "
                    f"not regime change."
                ),
                metadata={
                    "short_ma": short_ma,
                    "long_ma": long_ma,
                    "vol_ratio": vol_ratio,
                    "price_change": price_change,
                    "regime": regime,
                },
            )
        return Signal(
            strategy="momentum",
            asset=asset,
            direction="SHORT",
            confidence_bps=confidence_bps,
            entry_price=current_price,
            rationale=(
                f"MA crossover: short MA {short_ma:.2f} < long MA {long_ma:.2f}. "
                f"Volume {'confirmed' if vol_confirmed else 'weak'} "
                f"(ratio {vol_ratio:.2f}). Price down {price_change:.2%}."
            ),
            metadata={
                "short_ma": short_ma,
                "long_ma": long_ma,
                "vol_ratio": vol_ratio,
                "price_change": price_change,
                "regime": regime,
            },
        )
    else:
        strength = SignalStrength.WEAK.value
        return Signal(
            strategy="momentum",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=int(strength * 10000),
            entry_price=current_price,
            rationale=(
                f"MA crossover: short {short_ma:.2f} vs long {long_ma:.2f}. "
                f"No clear momentum signal."
            ),
            metadata={
                "short_ma": short_ma,
                "long_ma": long_ma,
                "vol_ratio": vol_ratio,
                "price_change": price_change,
            },
        )


def funding_rate_signal(
    asset: str,
    funding_rate: float,
    threshold: float = 0.001,
) -> Signal:
    """
    Directional contrarian bet on funding-rate extremes — NOT an arbitrage.

    Despite the historical name, this is a naked directional position: when
    funding is very positive (longs pay shorts) it SHORTS, expecting crowded
    positioning to revert. It carries full market risk; it does not hedge.
    The actual delta-neutral long-spot/short-perp package lives in
    agent.py::_run_funding_arb_package + multi_leg.py and runs through a
    separate path. The strategy id stays "funding_rate" for curator-profile
    compatibility (config/profiles.yaml allowlists); the honest description
    lives here and in every rationale string this signal emits.
    """
    if abs(funding_rate) < threshold:
        return Signal(
            strategy="funding_rate",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=int(0.3 * 10000),  # 30% confidence for weak signal
            entry_price=None,
            rationale=(
                f"Funding rate {funding_rate:.6f} within ±{threshold} band. "
                f"No significant signal."
            ),
            metadata={"funding_rate": funding_rate, "threshold": threshold},
        )

    # Confidence based on funding rate extremity
    # 0.1% funding = moderate, 0.5%+ = strong
    confidence = min(0.60 + abs(funding_rate) * 100, 0.90)
    confidence_bps = int(confidence * 10000)

    if funding_rate > threshold:
        # Longs pay shorts → contrarian SHORT signal (directional, not hedged)
        return Signal(
            strategy="funding_rate",
            asset=asset,
            direction="SHORT",
            confidence_bps=confidence_bps,
            entry_price=None,
            rationale=(
                f"Funding rate {funding_rate:.6f} strongly positive "
                f"(longs pay shorts). Directional contrarian short — "
                f"NOT a delta-neutral arb."
            ),
            metadata={"funding_rate": funding_rate, "threshold": threshold},
        )
    else:
        # Shorts pay longs → contrarian LONG signal (directional, not hedged)
        return Signal(
            strategy="funding_rate",
            asset=asset,
            direction="LONG",
            confidence_bps=confidence_bps,
            entry_price=None,
            rationale=(
                f"Funding rate {funding_rate:.6f} strongly negative "
                f"(shorts pay longs). Directional contrarian long — "
                f"NOT a delta-neutral arb."
            ),
            metadata={"funding_rate": funding_rate, "threshold": threshold},
        )


def ensemble_signal(asset: str, signals: list[Signal]) -> Signal:
    """
    Combine multiple signals into an ensemble decision.

    Weighted confidence voting with a correlated-evidence haircut: mean
    reversion and momentum both read the SAME price series, so their
    agreement is not independent confirmation. Within the price-action
    family, only the best signal counts at full weight; additional agreeing
    signals count at CORRELATED_EVIDENCE_WEIGHT. Funding-rate evidence comes
    from positioning data (a different source) and keeps full weight.
    """
    if not signals:
        return Signal(
            strategy="ensemble",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=0,
            rationale="No signals provided",
        )

    # Per-direction, per-family accumulation: family best at full weight,
    # rest of family at the haircut.
    long_score = 0.0
    short_score = 0.0
    long_conf_sum = 0.0
    short_conf_sum = 0.0

    for direction, score_acc, conf_acc in (
        ("LONG", "long", "long"),
        ("SHORT", "short", "short"),
    ):
        fam = [s for s in signals if s.direction == direction]
        if not fam:
            continue
        price_action = sorted(
            (
                s for s in fam
                if s.strategy in PRICE_ACTION_STRATEGIES
            ),
            key=lambda s: s.confidence_bps,
            reverse=True,
        )
        other = [s for s in fam if s.strategy not in PRICE_ACTION_STRATEGIES]

        def _contrib(s: Signal) -> float:
            conf = s.confidence_bps / 10000.0
            return conf * (1 if s.is_tradeable else 0.5)

        score = sum(_contrib(s) for s in other)
        if price_action:
            score += _contrib(price_action[0])
            score += CORRELATED_EVIDENCE_WEIGHT * sum(
                _contrib(s) for s in price_action[1:]
            )
        conf_sum = sum(s.confidence_bps / 10000.0 for s in fam)

        if direction == "LONG":
            long_score, long_conf_sum = score, conf_sum
        else:
            short_score, short_conf_sum = score, conf_sum

    total_conf = long_conf_sum + short_conf_sum
    if total_conf == 0:
        return Signal(
            strategy="ensemble",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=0,
            rationale=(
                f"All {len(signals)} signals neutral. No clear direction."
            ),
            metadata={"signals": [s.to_dict() for s in signals]},
        )

    haircut_note = (
        f" Price-action agreement haircut {CORRELATED_EVIDENCE_WEIGHT:.0%} applied "
        f"(mean-reversion/momentum share one price series)."
    )
    if long_score > short_score and long_score > 0:
        confidence = min(long_score / max(len(signals), 1), 0.95)
        return Signal(
            strategy="ensemble",
            asset=asset,
            direction="LONG",
            confidence_bps=int(confidence * 10000),
            entry_price=next(
                (s.entry_price for s in signals if s.entry_price), None
            ),
            rationale=(
                f"Ensemble: {sum(1 for s in signals if s.direction == 'LONG')}/"
                f"{len(signals)} signals bullish. "
                f"Weighted score {long_score:.2f} vs {short_score:.2f}."
                f"{haircut_note}"
            ),
            metadata={"signals": [s.to_dict() for s in signals]},
        )
    elif short_score > 0:
        confidence = min(short_score / max(len(signals), 1), 0.95)
        return Signal(
            strategy="ensemble",
            asset=asset,
            direction="SHORT",
            confidence_bps=int(confidence * 10000),
            entry_price=next(
                (s.entry_price for s in signals if s.entry_price), None
            ),
            rationale=(
                f"Ensemble: {sum(1 for s in signals if s.direction == 'SHORT')}/"
                f"{len(signals)} signals bearish. "
                f"Weighted score {long_score:.2f} vs {short_score:.2f}."
                f"{haircut_note}"
            ),
            metadata={"signals": [s.to_dict() for s in signals]},
        )

    return Signal(
        strategy="ensemble",
        asset=asset,
        direction="NEUTRAL",
        confidence_bps=0,
        rationale=f"No clear majority from {len(signals)} signals",
        metadata={"signals": [s.to_dict() for s in signals]},
    )


# --- Simple backtest helper ---

@dataclass
class BacktestResult:
    total_return_bps: int          # NET of costs — the honest headline number
    sharpe_ratio: float            # computed on NET per-trade returns
    max_drawdown_bps: int
    num_trades: int
    win_rate: float                # wins classified on NET pnl
    gross_return_bps: int = 0      # pre-cost return, for cost-drag visibility
    total_costs_usd: float = 0.0   # cumulative fees + slippage + funding paid


def backtest_simple(
    prices: list[float],
    signals: list[Signal],
    initial_capital: float = 10000,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    funding_cost_bps: float = 0.0,
) -> BacktestResult:
    """Basic backtest: execute tradeable signals, track PnL net of costs.

    Cost model (roadmap Phase 2: without this the backtest cannot answer
    "is this profitable"):
    - fee_bps: taker fee per side, charged on entry AND exit.
    - slippage_bps: adverse execution per side, charged on entry AND exit.
    - funding_cost_bps: perp funding paid once per round-trip hold. Sign is
      folded into the caller's value (a strategy collecting funding passes a
      negative number).

    Defaults are ZERO for backward compatibility with existing callers — but
    a zero-cost run is a GROSS run. Any validation meant to answer "does this
    have edge" MUST pass realistic values (e.g. X Layer perp takers ~5 bps
    fee, ~2-5 bps slippage on BTC/ETH majors, funding per holding period).

    Costs are charged on the full position notional (= current capital under
    this model's full-reinvest assumption), so compounding affects them too.
    """
    capital = initial_capital
    peak_capital = capital
    returns = []
    wins = 0
    losses = 0
    num_trades = 0
    total_costs = 0.0
    gross_pnl_total = 0.0

    # Round-trip cost rate on notional: both sides pay fee + slippage;
    # funding applies once per held position.
    cost_rate = (2 * (fee_bps + slippage_bps) + funding_cost_bps) / 10000.0

    for i, sig in enumerate(signals):
        if not sig.is_tradeable or i + 1 >= len(prices):
            continue

        num_trades += 1
        entry = prices[i]
        exit_price = prices[i + 1]

        if sig.direction == "LONG":
            gross_pnl = (exit_price - entry) / entry * capital
        elif sig.direction == "SHORT":
            gross_pnl = (entry - exit_price) / entry * capital
        else:
            continue

        costs = capital * cost_rate
        total_costs += costs
        gross_pnl_total += gross_pnl
        pnl = gross_pnl - costs

        capital += pnl
        peak_capital = max(peak_capital, capital)

        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1

        returns.append(pnl / initial_capital)

    net_return = (capital - initial_capital) / initial_capital
    net_return_bps = int(net_return * 10000)
    gross_return_bps = int((gross_pnl_total / initial_capital) * 10000)

    if returns:
        avg_return = statistics.mean(returns)
        std_return = statistics.pstdev(returns) if len(returns) > 1 else 0
        sharpe = avg_return / std_return if std_return > 0 else 0
    else:
        sharpe = 0

    max_dd = max((peak_capital - capital) / peak_capital if peak_capital > 0 else 0, 0)
    max_dd_bps = int(max_dd * 10000)

    win_rate = wins / num_trades if num_trades > 0 else 0

    return BacktestResult(
        total_return_bps=net_return_bps,
        sharpe_ratio=sharpe,
        max_drawdown_bps=max_dd_bps,
        num_trades=num_trades,
        win_rate=win_rate,
        gross_return_bps=gross_return_bps,
        total_costs_usd=total_costs,
    )


# ─── New Phase 2 Signals ───

def multi_timeframe_signal(
    asset: str,
    price_data_by_tf: dict[str, list[dict]],
    tf_weights: Optional[dict[str, float]] = None,
) -> Signal:
    """
    Multi-timeframe confirmation signal.

    Combines momentum signals across timeframes (e.g., 15m, 1H, 4H, 1D).
    Higher timeframe trend acts as filter; lower timeframe provides entry timing.

    price_data_by_tf: {"15m": [...], "1H": [...], "4H": [...], "1D": [...]}
    Each entry: list of {"close": float, "volume": float, "ts": int}
    """
    if not price_data_by_tf:
        return Signal(
            strategy="multi_timeframe",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=0,
            rationale="No timeframe data provided",
        )

    tf_weights = tf_weights or {
        "15m": 0.15,  # entry timing
        "1H": 0.25,   # short-term trend
        "4H": 0.30,   # swing trend
        "1D": 0.30,   # structural trend
    }

    # Get momentum signal for each timeframe
    tf_signals = {}
    for tf, data in price_data_by_tf.items():
        if len(data) >= 20:
            sig = momentum_signal(asset, data, short_window=5, long_window=20)
            tf_signals[tf] = sig

    if not tf_signals:
        return Signal(
            strategy="multi_timeframe",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=0,
            rationale="Insufficient data for any timeframe",
        )

    # Weighted vote
    long_weight = 0.0
    short_weight = 0.0
    total_weight = 0.0

    for tf, sig in tf_signals.items():
        weight = tf_weights.get(tf, 0.0)
        conf = sig.confidence_bps / 10000.0
        if sig.direction == "LONG":
            long_weight += weight * conf
        elif sig.direction == "SHORT":
            short_weight += weight * conf
        total_weight += weight

    # Require HTF alignment: 4H and 1D must agree (or be neutral)
    htf_long = all(
        tf_signals.get(tf, Signal(asset=asset, direction="NEUTRAL", confidence_bps=0, strategy="", rationale="")).direction
        in ("LONG", "NEUTRAL")
        for tf in ("4H", "1D")
    )
    htf_short = all(
        tf_signals.get(tf, Signal(asset=asset, direction="NEUTRAL", confidence_bps=0, strategy="", rationale="")).direction
        in ("SHORT", "NEUTRAL")
        for tf in ("4H", "1D")
    )

    rationale_parts = []
    for tf, sig in tf_signals.items():
        rationale_parts.append(f"{tf}: {sig.direction} ({sig.confidence_bps/100:.0f}%)")

    if long_weight > short_weight and htf_long:
        confidence = min(long_weight / max(total_weight, 0.01), 0.95)
        entry_tf = tf_signals.get("15m") or tf_signals.get("1H")
        return Signal(
            strategy="multi_timeframe",
            asset=asset,
            direction="LONG",
            confidence_bps=int(confidence * 10000),
            entry_price=entry_tf.entry_price if entry_tf else None,
            rationale=" | ".join(rationale_parts) + " → HTF aligned bullish",
            metadata={"tf_signals": {tf: s.to_dict() for tf, s in tf_signals.items()}},
        )
    elif short_weight > long_weight and htf_short:
        confidence = min(short_weight / max(total_weight, 0.01), 0.95)
        entry_tf = tf_signals.get("15m") or tf_signals.get("1H")
        return Signal(
            strategy="multi_timeframe",
            asset=asset,
            direction="SHORT",
            confidence_bps=int(confidence * 10000),
            entry_price=entry_tf.entry_price if entry_tf else None,
            rationale=" | ".join(rationale_parts) + " → HTF aligned bearish",
            metadata={"tf_signals": {tf: s.to_dict() for tf, s in tf_signals.items()}},
        )

    return Signal(
        strategy="multi_timeframe",
        asset=asset,
        direction="NEUTRAL",
        confidence_bps=0,
        rationale=" | ".join(rationale_parts) + " → HTF conflict or no clear bias",
        metadata={"tf_signals": {tf: s.to_dict() for tf, s in tf_signals.items()}},
    )


def volume_weighted_signal(
    asset: str,
    price_data: list[dict],
    window: int = 20,
) -> Signal:
    """
    Volume-weighted price action signal.

    Uses VWAP deviation + volume profile to confirm moves.
    High volume on move = conviction; low volume = potential fakeout.

    price_data: list of {"close": float, "high": float, "low": float, "volume": float, "ts": int}
    """
    if len(price_data) < window:
        return Signal(
            strategy="volume_weighted",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=0,
            rationale=f"Insufficient data: {len(price_data)} < {window}",
        )

    recent = price_data[-window:]

    # Calculate VWAP
    total_pv = sum(d["close"] * d["volume"] for d in recent)
    total_vol = sum(d["volume"] for d in recent)
    vwap = total_pv / total_vol if total_vol > 0 else recent[-1]["close"]

    current = recent[-1]["close"]
    deviation_pct = (current - vwap) / vwap * 100

    # Volume trend: is volume increasing on the move?
    first_half_vol = statistics.mean(d["volume"] for d in recent[:window//2])
    second_half_vol = statistics.mean(d["volume"] for d in recent[window//2:])
    vol_trend = second_half_vol / first_half_vol if first_half_vol > 0 else 1.0

    # Price volatility
    returns = [(recent[i]["close"] - recent[i-1]["close"]) / recent[i-1]["close"]
               for i in range(1, len(recent))]
    volatility = statistics.pstdev(returns) if len(returns) > 1 else 0

    # Signal logic
    if deviation_pct > 1.0 and vol_trend > 1.1:
        # Price above VWAP with rising volume = bullish conviction
        confidence = min(0.6 + abs(deviation_pct) * 0.1 + (vol_trend - 1) * 0.2, 0.9)
        return Signal(
            strategy="volume_weighted",
            asset=asset,
            direction="LONG",
            confidence_bps=int(confidence * 10000),
            entry_price=current,
            rationale=(
                f"Price {deviation_pct:.1f}% above VWAP ({vwap:.2f}), "
                f"volume trending up {vol_trend:.2f}x. Bullish conviction."
            ),
            metadata={"vwap": vwap, "deviation_pct": deviation_pct, "vol_trend": vol_trend, "volatility": volatility},
        )
    elif deviation_pct < -1.0 and vol_trend > 1.1:
        # Price below VWAP with rising volume = bearish conviction
        confidence = min(0.6 + abs(deviation_pct) * 0.1 + (vol_trend - 1) * 0.2, 0.9)
        return Signal(
            strategy="volume_weighted",
            asset=asset,
            direction="SHORT",
            confidence_bps=int(confidence * 10000),
            entry_price=current,
            rationale=(
                f"Price {abs(deviation_pct):.1f}% below VWAP ({vwap:.2f}), "
                f"volume trending up {vol_trend:.2f}x. Bearish conviction."
            ),
            metadata={"vwap": vwap, "deviation_pct": deviation_pct, "vol_trend": vol_trend, "volatility": volatility},
        )
    elif abs(deviation_pct) < 0.5:
        # Near VWAP = equilibrium, no signal
        return Signal(
            strategy="volume_weighted",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=3000,
            entry_price=current,
            rationale=f"Price near VWAP ({deviation_pct:+.1f}%). Equilibrium.",
            metadata={"vwap": vwap, "deviation_pct": deviation_pct, "vol_trend": vol_trend},
        )
    else:
        # Deviation but no volume confirmation = potential fakeout
        return Signal(
            strategy="volume_weighted",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=2000,
            entry_price=current,
            rationale=(
                f"Price {deviation_pct:+.1f}% from VWAP but volume trend {vol_trend:.2f}x "
                f"(no confirmation). Potential fakeout."
            ),
            metadata={"vwap": vwap, "deviation_pct": deviation_pct, "vol_trend": vol_trend, "volatility": volatility},
        )


def funding_carry_signal(
    asset: str,
    spot_price: float,
    perp_price: float,
    funding_rate: float,
    next_funding_ts: int,
    min_basis_bps: float = 5.0,
    min_annualized_apr: float = 10.0,
) -> Signal:
    """
    Funding carry / basis trade signal (delta-neutral).

    Long spot + short perp when:
    - Perp trades at premium to spot (positive basis)
    - Funding rate positive (longs pay shorts)
    - Annualized carry exceeds threshold

    This is a TRUE delta-neutral carry trade, NOT directional.
    """
    basis = perp_price - spot_price
    basis_bps = (basis / spot_price) * 10000 if spot_price > 0 else 0

    # Annualized funding (8 funding periods/day = 365*8 = 2920 periods/year)
    annualized_funding = funding_rate * 2920 * 100  # as percentage

    if basis_bps < min_basis_bps or annualized_funding < min_annualized_apr:
        return Signal(
            strategy="funding_carry",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=0,
            entry_price=spot_price,
            rationale=(
                f"Basis {basis_bps:.1f}bps (< {min_basis_bps}) or "
                f"annualized funding {annualized_funding:.1f}% (< {min_annualized_apr}%). "
                f"No carry opportunity."
            ),
            metadata={
                "spot_price": spot_price,
                "perp_price": perp_price,
                "basis_bps": basis_bps,
                "funding_rate": funding_rate,
                "annualized_funding_pct": annualized_funding,
            },
        )

    # Long spot, short perp = collect funding while hedged
    confidence = min(0.7 + basis_bps / 200 + annualized_funding / 200, 0.9)
    return Signal(
        strategy="funding_carry",
        asset=asset,
        direction="LONG",  # direction = long spot leg (short perp is implicit)
        confidence_bps=int(confidence * 10000),
        entry_price=spot_price,
        rationale=(
            f"Carry trade: basis {basis_bps:.1f}bps, "
            f"funding {funding_rate:.6f} ({annualized_funding:.1f}% APR). "
            f"Long spot + short perp to collect carry."
        ),
        metadata={
            "spot_price": spot_price,
            "perp_price": perp_price,
            "basis_bps": basis_bps,
            "funding_rate": funding_rate,
            "annualized_funding_pct": annualized_funding,
            "next_funding_ts": next_funding_ts,
            "legs": {"spot": "LONG", "perp": "SHORT"},
        },
    )


def onchain_flow_signal(
    asset: str,
    whale_net_flow_usd: float,        # net whale inflow (+) / outflow (-) in USD
    exchange_reserve_change_pct: float,  # % change in exchange reserves (negative = accumulation)
    stablecoin_supply_change_pct: float, # % change in stablecoin supply (positive = buying power)
    min_whale_flow_usd: float = 10_000_000,
) -> Signal:
    """
    On-chain flow signal from whale/exchange metrics.

    Bullish: whale accumulation (+), exchange reserves dropping (-), stablecoin supply growing (+)
    Bearish: whale distribution (-), exchange reserves rising (+), stablecoin supply shrinking (-)

    Data sources (to be fetched externally):
    - Whale alerts: large txs > $100k
    - Exchange reserves: Glassnode / CryptoQuant / on-chain queries
    - Stablecoin supply: DefiLlama / on-chain
    """
    bullish_score = 0
    bearish_score = 0
    reasons = []

    if abs(whale_net_flow_usd) >= min_whale_flow_usd:
        if whale_net_flow_usd > 0:
            bullish_score += 1
            reasons.append(f"whale inflow ${whale_net_flow_usd/1e6:.1f}M")
        else:
            bearish_score += 1
            reasons.append(f"whale outflow ${abs(whale_net_flow_usd)/1e6:.1f}M")

    if abs(exchange_reserve_change_pct) >= 1.0:
        if exchange_reserve_change_pct < 0:
            bullish_score += 1
            reasons.append(f"exchange reserves down {abs(exchange_reserve_change_pct):.1f}% (accumulation)")
        else:
            bearish_score += 1
            reasons.append(f"exchange reserves up {exchange_reserve_change_pct:.1f}% (selling pressure)")

    if abs(stablecoin_supply_change_pct) >= 2.0:
        if stablecoin_supply_change_pct > 0:
            bullish_score += 1
            reasons.append(f"stablecoin supply up {stablecoin_supply_change_pct:.1f}% (dry powder)")
        else:
            bearish_score += 1
            reasons.append(f"stablecoin supply down {abs(stablecoin_supply_change_pct):.1f}% (risk-off)")

    total_signals = bullish_score + bearish_score
    if total_signals == 0:
        return Signal(
            strategy="onchain_flow",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=0,
            rationale="No significant on-chain flow signals",
            metadata={
                "whale_net_flow_usd": whale_net_flow_usd,
                "exchange_reserve_change_pct": exchange_reserve_change_pct,
                "stablecoin_supply_change_pct": stablecoin_supply_change_pct,
            },
        )

    if bullish_score > bearish_score:
        confidence = min(0.5 + bullish_score * 0.15, 0.85)
        return Signal(
            strategy="onchain_flow",
            asset=asset,
            direction="LONG",
            confidence_bps=int(confidence * 10000),
            rationale="On-chain bullish: " + "; ".join(reasons),
            metadata={
                "whale_net_flow_usd": whale_net_flow_usd,
                "exchange_reserve_change_pct": exchange_reserve_change_pct,
                "stablecoin_supply_change_pct": stablecoin_supply_change_pct,
                "bullish_score": bullish_score,
                "bearish_score": bearish_score,
            },
        )
    elif bearish_score > 0:
        confidence = min(0.5 + bearish_score * 0.15, 0.85)
        return Signal(
            strategy="onchain_flow",
            asset=asset,
            direction="SHORT",
            confidence_bps=int(confidence * 10000),
            rationale="On-chain bearish: " + "; ".join(reasons),
            metadata={
                "whale_net_flow_usd": whale_net_flow_usd,
                "exchange_reserve_change_pct": exchange_reserve_change_pct,
                "stablecoin_supply_change_pct": stablecoin_supply_change_pct,
                "bullish_score": bullish_score,
                "bearish_score": bearish_score,
            },
        )

    return Signal(
        strategy="onchain_flow",
        asset=asset,
        direction="NEUTRAL",
        confidence_bps=0,
        rationale="On-chain flows conflicted",
        metadata={
            "whale_net_flow_usd": whale_net_flow_usd,
            "exchange_reserve_change_pct": exchange_reserve_change_pct,
            "stablecoin_supply_change_pct": stablecoin_supply_change_pct,
        },
    )
