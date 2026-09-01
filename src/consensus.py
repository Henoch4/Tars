"""
ConsensusGate — Product B (Swarm Trading).

Sits between trader layer and RiskGate. Computes cohort-based quorum
from independent trader outputs. Replaces the weighted-average ensemble.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional

from .signals import Signal, SignalDirection
from .trader import TraderStrategy, MarketContext


class ConsensusBehavior(Enum):
    """What to do when consensus threshold isn't met."""
    SKIP = "skip"                    # No trade this cycle
    REDUCED_SIZE = "reduced_size"    # Trade at reduced position (e.g., 50%)
    DEFAULT_TRADER = "default_trader" # Fall back to designated default trader
    HOLD_AND_LOG = "hold_and_log"    # Hold position, log no-consensus event


@dataclass
class TraderVote:
    """Single trader's vote in the consensus."""
    trader_name: str
    cohort: str
    weight: float
    signal: Signal
    vote_weight: float  # weight * confidence


@dataclass
class ConsensusResult:
    """Result of consensus computation for one asset."""
    asset: str
    cohort: str
    votes: list[TraderVote]
    long_weight: float
    short_weight: float
    total_weight: float
    threshold: float
    consensus_reached: bool
    direction: SignalDirection
    consensus_confidence_bps: int
    rationale: str
    behavior: ConsensusBehavior
    default_signal: Signal | None = None


class ConsensusGate:
    """
    Cohort-based consensus gate. Replaces weighted-average ensemble.
    
    Key design (per design doc §4.2):
    - Consensus computed PER ASSET-CLASS COHORT (majors, alts, memecoins, etc.)
    - Traders assigned to cohorts; only traders in same cohort vote together
    - Threshold is percentage of total cohort weight that must agree
    - Behavior when threshold not met is configurable
    """
    
    def __init__(
        self,
        thresholds: dict[str, float] | None = None,
        default_behavior: ConsensusBehavior = ConsensusBehavior.SKIP,
        default_trader_name: str | None = None,
    ):
        """
        Args:
            thresholds: {cohort: threshold_pct} — minimum % of cohort weight
                       required for consensus (e.g., {"majors": 0.65, "alts": 0.75})
            default_behavior: What to do when threshold not met
            default_trader_name: Name of trader to use for DEFAULT_TRADER behavior
        """
        self.thresholds = thresholds or {
            "majors": 0.65,      # 65% of cohort weight must agree
            "alts": 0.75,        # Higher bar for less liquid assets
            "memecoins": 0.80,   # Highest bar for highest noise
            "tokenized_stocks": 0.70,
        }
        self.default_behavior = default_behavior
        self.default_trader_name = default_trader_name
    
    def get_threshold(self, cohort: str) -> float:
        return self.thresholds.get(cohort, 0.65)
    
    def set_threshold(self, cohort: str, threshold: float) -> None:
        self.thresholds[cohort] = threshold
    
    def compute_consensus(
        self,
        asset: str,
        cohort: str,
        traders: list[TraderStrategy],
        context: MarketContext,
    ) -> ConsensusResult:
        """
        Compute consensus from trader votes for a single asset.
        
        Args:
            asset: Asset symbol (e.g., "BTC-USDT-SWAP")
            cohort: Asset class cohort (e.g., "majors")
            traders: List of TraderStrategy instances for this cohort
            context: MarketContext for signal generation
            
        Returns:
            ConsensusResult with vote breakdown and decision
        """
        threshold = self.get_threshold(cohort)
        
        # Collect votes from all traders in cohort
        votes: list[TraderVote] = []
        for trader in traders:
            if not trader.config.enabled:
                continue
            if trader.config.asset_class_cohort != cohort:
                continue  # Trader not in this cohort
            
            signal = trader.on_data(context)
            vote_weight = trader.config.weight * (signal.confidence_bps / 10000.0)
            
            votes.append(TraderVote(
                trader_name=trader.name,
                cohort=cohort,
                weight=trader.config.weight,
                signal=signal,
                vote_weight=vote_weight,
            ))
        
        if not votes:
            return ConsensusResult(
                asset=asset,
                cohort=cohort,
                votes=[],
                long_weight=0.0,
                short_weight=0.0,
                total_weight=0.0,
                threshold=threshold,
                consensus_reached=False,
                direction="NEUTRAL",
                consensus_confidence_bps=0,
                rationale="No traders in cohort or all disabled",
                behavior=self.default_behavior,
            )
        
        # Aggregate by direction
        long_weight = sum(v.vote_weight for v in votes if v.signal.direction == "LONG")
        short_weight = sum(v.vote_weight for v in votes if v.signal.direction == "SHORT")
        neutral_weight = sum(v.vote_weight for v in votes if v.signal.direction == "NEUTRAL")
        total_weight = long_weight + short_weight + neutral_weight
        
        if total_weight == 0:
            return ConsensusResult(
                asset=asset,
                cohort=cohort,
                votes=votes,
                long_weight=0.0,
                short_weight=0.0,
                total_weight=0.0,
                threshold=threshold,
                consensus_reached=False,
                direction="NEUTRAL",
                consensus_confidence_bps=0,
                rationale="All traders returned NEUTRAL",
                behavior=self.default_behavior,
            )
        
        # Determine consensus direction and weight
        if long_weight > short_weight:
            winning_weight = long_weight
            winning_direction: SignalDirection = "LONG"
        elif short_weight > long_weight:
            winning_weight = short_weight
            winning_direction = "SHORT"
        else:
            winning_weight = 0.0
            winning_direction = "NEUTRAL"
        
        consensus_pct = winning_weight / total_weight if total_weight > 0 else 0.0
        consensus_reached = consensus_pct >= threshold
        
        # Calculate consensus confidence (0-10000 bps)
        consensus_confidence_bps = int(consensus_pct * 10000) if consensus_reached else 0
        
        # Build rationale
        vote_details = []
        for v in votes:
            vote_details.append(
                f"{v.trader_name}: {v.signal.direction} ({v.signal.confidence_bps/100:.0f}%, w={v.weight})"
            )
        rationale = (
            f"Cohort={cohort}, threshold={threshold:.0%}, "
            f"consensus={consensus_pct:.1%} {'✓' if consensus_reached else '✗'}. "
            f"LONG={long_weight:.2f} SHORT={short_weight:.2f} NEUTRAL={neutral_weight:.2f}. "
            f"Votes: {'; '.join(vote_details)}"
        )
        
        # Determine behavior if no consensus
        behavior = ConsensusBehavior.SKIP
        default_signal = None
        
        if not consensus_reached:
            behavior = self.default_behavior
            if behavior == ConsensusBehavior.DEFAULT_TRADER and self.default_trader_name:
                # Find default trader and get its signal
                for trader in traders:
                    if trader.name == self.default_trader_name:
                        default_signal = trader.on_data(context)
                        break
        
        return ConsensusResult(
            asset=asset,
            cohort=cohort,
            votes=votes,
            long_weight=long_weight,
            short_weight=short_weight,
            total_weight=total_weight,
            threshold=threshold,
            consensus_reached=consensus_reached,
            direction=winning_direction if consensus_reached else "NEUTRAL",
            consensus_confidence_bps=consensus_confidence_bps,
            rationale=rationale,
            behavior=behavior,
            default_signal=default_signal,
        )
    
    def compute_all_assets(
        self,
        contexts: dict[str, MarketContext],
        traders_by_cohort: dict[str, list[TraderStrategy]],
    ) -> dict[str, ConsensusResult]:
        """
        Compute consensus for multiple assets grouped by cohort.
        
        Args:
            contexts: {asset: MarketContext}
            traders_by_cohort: {cohort: [TraderStrategy, ...]}
            
        Returns:
            {asset: ConsensusResult}
        """
        results = {}
        
        for cohort, traders in traders_by_cohort.items():
            for asset, context in contexts.items():
                if not traders:
                    continue
                result = self.compute_consensus(asset, cohort, traders, context)
                results[asset] = result
        
        return results


# ─── Default ConsensusGate for majors cohort ───

def create_default_consensus_gate() -> ConsensusGate:
    """Create consensus gate with default thresholds for majors."""
    return ConsensusGate(
        thresholds={
            "majors": 0.65,
            "alts": 0.75,
            "memecoins": 0.80,
            "tokenized_stocks": 0.70,
        },
        default_behavior=ConsensusBehavior.SKIP,
        default_trader_name="funding_carry",  # Fallback to delta-neutral arb
    )