"""
Trading signal engine for the autonomous trading agent.

Implements multiple signal strategies with confidence scoring:
  - Mean reversion (Z-score based on rolling window)
  - Momentum (price trend + volume confirmation)
  - Funding rate arbitrage signal
  - Tokenized stock carry (Merton cost-of-carry)
  - Ensemble combination with correlated-evidence haircut

Each signal returns a Signal object with direction, confidence, and rationale.
The risk engine then gates these before any execution.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional

from .bsm import bs_price, bs_price_merton, greeks
from .stock_carry import tokenized_stock_carry_signal, vault_9010_allocation

try:
    from .regime_hmm import RegimeHMM, infer_regime_simple
except ImportError:
    RegimeHMM = None
    infer_regime_simple = None


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
    asset: str          # e.g. "AAPLx"
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


# --- Regime filter ---

PRICE_ACTION_STRATEGIES = frozenset({"mean_reversion", "momentum"})
CORRELATED_EVIDENCE_WEIGHT = 0.5


def trend_regime(prices: list[float], regime_window: int = 50) -> dict:
    """Classify the structural trend over a window LONGER than any signal's lookback."""
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

    regime = (
        trend_regime(prices, regime_window)
        if regime_window > 0
        else {"regime": "unknown", "slope_pct": 0.0}
    )

    confidence_factor = min(abs(z_score) / z_threshold, 1.0)
    base_confidence = 0.60
    confidence = base_confidence + (0.35 * confidence_factor)
    confidence_bps = int(confidence * 10000)

    if z_score < -z_threshold:
        if regime["regime"] == "down":
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
                metadata={"z_score": z_score, "mean": mean_price, "std": std_price,
                          "current_price": current_price, "regime": regime},
            )
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
            metadata={"z_score": z_score, "mean": mean_price, "std": std_price,
                      "current_price": current_price, "regime": regime},
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
                metadata={"z_score": z_score, "mean": mean_price, "std": std_price,
                          "current_price": current_price, "regime": regime},
            )
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
            metadata={"z_score": z_score, "mean": mean_price, "std": std_price,
                      "current_price": current_price, "regime": regime},
        )
    else:
        strength = SignalStrength.WEAK.value
        return Signal(
            strategy="mean_reversion",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=int(strength * 10000),
            entry_price=current_price,
            rationale=f"Z-score {z_score:.2f} within ±{z_threshold} band. No mean-reversion opportunity.",
            metadata={"z_score": z_score, "mean": mean_price, "std": std_price, "current_price": current_price},
        )


def momentum_signal(
    asset: str,
    price_data: list[dict],
    short_window: int = 5,
    long_window: int = 20,
    regime_window: int = 0,
) -> Signal:
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

    recent_volumes = [d["volume"] for d in price_data[-short_window:]]
    avg_volume = statistics.mean(recent_volumes) if recent_volumes else 0
    vol_ratio = recent_volumes[-1] / avg_volume if avg_volume > 0 else 1.0
    vol_confirmed = vol_ratio > 0.8

    price_change = (current_price - long_ma) / long_ma if long_ma > 0 else 0

    closes_all = [d["close"] for d in price_data]
    regime = (
        trend_regime(closes_all, regime_window)
        if regime_window > 0
        else {"regime": "unknown", "slope_pct": 0.0}
    )

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
                metadata={"short_ma": short_ma, "long_ma": long_ma,
                          "vol_ratio": vol_ratio, "price_change": price_change, "regime": regime},
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
            metadata={"short_ma": short_ma, "long_ma": long_ma,
                      "vol_ratio": vol_ratio, "price_change": price_change, "regime": regime},
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
                metadata={"short_ma": short_ma, "long_ma": long_ma,
                          "vol_ratio": vol_ratio, "price_change": price_change, "regime": regime},
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
            metadata={"short_ma": short_ma, "long_ma": long_ma,
                      "vol_ratio": vol_ratio, "price_change": price_change, "regime": regime},
        )
    else:
        strength = SignalStrength.WEAK.value
        return Signal(
            strategy="momentum",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=int(strength * 10000),
            entry_price=current_price,
            rationale=f"MA crossover: short {short_ma:.2f} vs long {long_ma:.2f}. No clear momentum signal.",
            metadata={"short_ma": short_ma, "long_ma": long_ma, "vol_ratio": vol_ratio, "price_change": price_change},
        )


def funding_rate_signal(
    asset: str,
    funding_rate: float,
    threshold: float = 0.001,
) -> Signal:
    if abs(funding_rate) < threshold:
        return Signal(
            strategy="funding_rate",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=int(0.3 * 10000),
            entry_price=None,
            rationale=f"Funding rate {funding_rate:.6f} within ±{threshold} band. No significant signal.",
            metadata={"funding_rate": funding_rate, "threshold": threshold},
        )

    confidence = min(0.60 + abs(funding_rate) * 100, 0.90)
    confidence_bps = int(confidence * 10000)

    if funding_rate > threshold:
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


def stock_carry_signal(
    asset: str,
    spot_price: float,
    perp_price: float,
    borrow_rate: float,
    div_yield: float,
    funding_rate: float,
    fee_drag: float = 0.0,
    min_basis_bps: float = 5.0,
    min_carry_bps: float = 50.0,
) -> Signal:
    """Wrapper that delegates to stock_carry.py implementation."""
    from .stock_carry import tokenized_stock_carry_signal
    return tokenized_stock_carry_signal(
        asset, spot_price, perp_price, borrow_rate, div_yield, funding_rate, fee_drag,
        min_basis_bps, min_carry_bps
    )


def ensemble_signal(asset: str, signals: list[Signal]) -> Signal:
    """Combine multiple signals into an ensemble decision with correlated-evidence haircut."""
    if not signals:
        return Signal(
            strategy="ensemble",
            asset=asset,
            direction="NEUTRAL",
            confidence_bps=0,
            rationale="No signals provided",
        )

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
            (s for s in fam if s.strategy in PRICE_ACTION_STRATEGIES),
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
            rationale=f"All {len(signals)} signals neutral. No clear direction.",
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
            entry_price=next((s.entry_price for s in signals if s.entry_price), None),
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
            entry_price=next((s.entry_price for s in signals if s.entry_price), None),
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


def generate_signals(
    asset: str,
    prices: list[float],
    price_data: list[dict],
    funding_rate: float,
    spot_price: float,
    perp_price: float,
    borrow_rate: float,
    div_yield: float,
    regime_window: int = 50,
) -> list[Signal]:
    """Generate all signals for an asset."""
    signals = [
        mean_reversion_signal(asset, prices, regime_window=regime_window),
        momentum_signal(asset, price_data, regime_window=regime_window),
        funding_rate_signal(asset, funding_rate),
    ]

    # Add stock carry signal if we have the data
    if spot_price and perp_price and borrow_rate is not None:
        signals.append(stock_carry_signal(asset, spot_price, perp_price, borrow_rate, div_yield, 0.0))

    return signals