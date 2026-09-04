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
    vote_weight: float  # effective_weight * confidence
    effective_weight: float = 0.0  # base weight, scaled down when quarantined
    quarantined: bool = False


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
    quarantined: list[str] = field(default_factory=list)


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
        # --- Divergence quarantine (resurrected ika BFT drop) ---
        # Fault-detection principle, not network consensus: a trader that
        # votes against the cohort majority for `divergence_window`
        # consecutive observations, emits NaN confidences, or lock-steps
        # another trader's votes gets its effective weight scaled by
        # `quarantine_factor` (never excluded — a silenced trader can't be
        # audited, and the cohort itself may be the wrong one). Release
        # after `release_window` consecutive agreements. 0/False disables.
        divergence_window: int = 8,
        quarantine_factor: float = 0.25,
        release_window: int = 8,
        enable_quarantine: bool = True,
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
        self.divergence_window = divergence_window
        self.quarantine_factor = quarantine_factor
        self.release_window = release_window
        self.enable_quarantine = enable_quarantine
        # Per-trader health, keyed by trader name: against/agree streaks,
        # quarantined flag + reason, last vote signature, NaN count.
        # An observation = one compute_consensus call (≈ assets × cycles).
        self._health: dict[str, dict] = {}
        self._copy_streaks: dict[frozenset, int] = {}
    
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
            # NaN confidence poisons every downstream sum into NaN and
            # silently kills the whole cohort's math — sanitize to 0 and
            # count it as a degenerate event, never propagate it.
            try:
                confidence = float(signal.confidence_bps)
            except (TypeError, ValueError):
                confidence = float("nan")
            nan_output = confidence != confidence  # NaN is the only != self
            if nan_output:
                confidence = 0.0
                self._note_degenerate(trader.name, "nan_output")

            health = self._health.get(trader.name)
            quarantined = bool(health and health.get("quarantined"))
            effective = (trader.config.weight * self.quarantine_factor
                         if quarantined and self.enable_quarantine
                         else trader.config.weight)
            vote_weight = effective * (confidence / 10000.0)

            votes.append(TraderVote(
                trader_name=trader.name,
                cohort=cohort,
                weight=trader.config.weight,
                signal=signal,
                vote_weight=vote_weight,
                effective_weight=effective,
                quarantined=quarantined and self.enable_quarantine,
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
        
        # Divergence quarantine bookkeeping (fault detection, not ordering).
        quarantined_now = self._update_health(votes, winning_direction)

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
        if quarantined_now:
            rationale += f" Quarantined: {', '.join(sorted(quarantined_now))}."
        
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
            quarantined=sorted(quarantined_now),
        )
    
    def _note_degenerate(self, trader_name: str, reason: str) -> None:
        """Count a degenerate output (currently NaN confidence)."""
        health = self._health.setdefault(
            trader_name, {"against": 0, "agree": 0, "quarantined": False,
                          "reason": None, "last_sig": None, "nan": 0})
        health["nan"] += 1
        if (self.enable_quarantine and self.divergence_window > 0
                and health["nan"] >= self.divergence_window
                and not health["quarantined"]):
            self._quarantine(trader_name, reason)

    def _quarantine(self, trader_name: str, reason: str) -> None:
        """Down-weight a trader. Logs + counts; never excludes."""
        import logging
        health = self._health.setdefault(
            trader_name, {"against": 0, "agree": 0, "quarantined": False,
                          "reason": None, "last_sig": None, "nan": 0})
        health["quarantined"] = True
        health["reason"] = reason
        health["agree"] = 0
        logging.getLogger(__name__).warning(
            f"Consensus quarantine: {trader_name} [{reason}] — effective "
            f"weight x{self.quarantine_factor}")
        try:
            from .metrics import inc as _metrics_inc
            _metrics_inc("tars_trader_quarantines_total",
                         {"trader": trader_name, "reason": reason})
        except Exception:
            pass

    def _release(self, trader_name: str) -> None:
        import logging
        health = self._health[trader_name]
        health["quarantined"] = False
        health["reason"] = None
        health["against"] = 0
        health["agree"] = 0
        health["nan"] = 0
        # Drop pair streaks containing this trader: without this, the
        # still-counting streak would re-quarantine on the very next round
        # and release could never stick.
        for pair in list(self._copy_streaks):
            if trader_name in pair:
                del self._copy_streaks[pair]
        logging.getLogger(__name__).warning(
            f"Consensus release: {trader_name} voting with cohort again")

    def _update_health(self, votes: list[TraderVote],
                       winning_direction: SignalDirection) -> set[str]:
        """Per-trader fault detection over this round's votes.

        Tracks divergence streaks (voting against a decided majority),
        lock-step copies (identical direction+confidence as another voter),
        and releases on sustained agreement. All-NEUTRAL rounds count as
        agreement for everyone — in a no-edge regime neutrality is the
        honest vote, not a pathology. Returns currently-quarantined names.
        """
        if not self.enable_quarantine:
            return set()
        for v in votes:
            h = self._health.setdefault(
                v.trader_name, {"against": 0, "agree": 0, "quarantined": False,
                                "reason": None, "last_sig": None, "nan": 0})
            if winning_direction == "NEUTRAL":
                h["agree"] += 1
                h["against"] = 0
            elif v.signal.direction == winning_direction:
                h["agree"] += 1
                h["against"] = 0
            else:
                h["against"] += 1
                h["agree"] = 0
            h["last_sig"] = (v.signal.direction, v.signal.confidence_bps)
            if (not h["quarantined"] and self.divergence_window > 0
                    and h["against"] >= self.divergence_window):
                self._quarantine(v.trader_name, "diverged")
            elif (h["quarantined"] and self.release_window > 0
                    and h["agree"] >= self.release_window):
                self._release(v.trader_name)

        # Lock-step copies: identical (direction, confidence) as another
        # voter for `divergence_window` straight observations. The lower
        # base weight is quarantined (tiebreak: larger name) — one of the
        # two is redundant or a copy bug. NEUTRAL pairs are exempt:
        # joint abstention in a no-edge regime is the honest vote, not
        # evidence of copying (directional conviction is what copies).
        if self.divergence_window > 0:
            by_sig: dict[tuple, list[TraderVote]] = {}
            for v in votes:
                if v.signal.direction == "NEUTRAL":
                    continue
                by_sig.setdefault(
                    (v.signal.direction, v.signal.confidence_bps), []).append(v)
            seen_pairs = set()
            for sig_votes in by_sig.values():
                names = sorted(v.trader_name for v in sig_votes)
                for i in range(len(names)):
                    for j in range(i + 1, len(names)):
                        pair = frozenset((names[i], names[j]))
                        seen_pairs.add(pair)
                        self._copy_streaks[pair] = (
                            self._copy_streaks.get(pair, 0) + 1)
                        if self._copy_streaks[pair] >= self.divergence_window:
                            weights = {v.trader_name: v.weight for v in votes
                                       if v.trader_name in pair}
                            lo, hi = min(pair), max(pair)
                            # Lower base weight goes; ties quarantine the
                            # larger name so the outcome is deterministic.
                            target = lo if weights.get(lo, 0.0) < weights.get(hi, 0.0) else hi
                            h = self._health[target]
                            if not h["quarantined"]:
                                self._quarantine(target, "lockstep")
                            # Reset the streak: re-quarantine needs a fresh
                            # full window of copying, or release can never
                            # stick (the counter would re-fire next round).
                            self._copy_streaks[pair] = 0
            for pair in list(self._copy_streaks):
                if pair not in seen_pairs:
                    del self._copy_streaks[pair]

        return {name for name, h in self._health.items() if h["quarantined"]}

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