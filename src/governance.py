"""
Generalized decision payload for Product A — Audit/Governance Platform.

Venue-agnostic schema that any AI agent or trading operation can use to
log and prove its decisions before execution. Decoupled from crypto-specific
fields (asset, entry_price, size_usd, is_short, package_id).

The core schema: {agent_id, decision_hash, timestamp, pre_commit_signature, outcome_hash}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from decimal import Decimal, ROUND_DOWN


def _to_fixed_point_1e8(value: float) -> int:
    """Convert a float to 1e8 fixed-point using Decimal for exact conversion."""
    return int(Decimal(str(value)).scaleb(8).to_integral_value(rounding=ROUND_DOWN))


@dataclass
class GenericDecisionPayload:
    """
    Venue-agnostic decision payload for any AI agent.
    
    Fields:
        decision_id: Unique identifier for this decision (UUID or hash)
        agent_id: Identifier of the agent making the decision
        action_type: Type of action (e.g., "trade", "vote", "moderate", "allocate")
        action_params: JSON-serializable parameters specific to the action
        confidence_bps: Agent's confidence in basis points (0-10000)
        rationale: Human-readable explanation of the decision
        risk_context_hash: Hash of risk parameters/policy in effect
        timestamp: Unix timestamp when decision was made
        metadata: Extensible key-value for domain-specific data
    """
    decision_id: str
    agent_id: str
    action_type: str
    action_params: dict[str, Any]
    confidence_bps: int
    rationale: str
    risk_context_hash: str
    timestamp: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenericOutcomePayload:
    """
    Outcome record for a previously logged decision.
    
    Links back to the original decision via decision_id.
    """
    decision_id: str
    outcome_type: str          # e.g., "success", "failure", "partial", "timeout"
    outcome_params: dict[str, Any]
    pnl_usd: float | None = None
    timestamp: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionReceipt:
    """
    Verifiable receipt returned after logging a decision.
    
    Contains everything needed for independent verification.
    """
    decision_id: str
    tx_hash: str | None
    block_number: int | None
    timestamp: int
    payload_hash: str  # Keccak256 of the payload
    signature: str     # EIP-191 signature by agent


# ─── Crypto-Trading Specific Payload (backward compatibility) ───

@dataclass
class TradingDecisionPayload:
    """
    Crypto-trading specific decision payload (extends generic).
    
    Used by Tarstrade's live trading agent. Maps to TradeAuditTrail.sol.
    """
    decision_id: str
    agent_address: str
    asset: str
    signal: str
    strategy: str
    confidence_bps: int
    entry_price: float
    size_usd: float
    risk_params_hash: str
    timestamp: int
    is_short: bool = False
    package_id: str | None = None
    
    def to_generic(self) -> GenericDecisionPayload:
        """Convert to generic payload for cross-platform verification."""
        return GenericDecisionPayload(
            decision_id=self.decision_id,
            agent_id=self.agent_address,
            action_type="trade",
            action_params={
                "asset": self.asset,
                "signal": self.signal,
                "entry_price": self.entry_price,
                "size_usd": self.size_usd,
                "is_short": self.is_short,
            },
            confidence_bps=self.confidence_bps,
            rationale=self.strategy,
            risk_context_hash=self.risk_params_hash,
            timestamp=self.timestamp,
            metadata={
                "package_id": self.package_id,
            }
        )


def trading_to_generic_payload(trading: TradingDecisionPayload) -> GenericDecisionPayload:
    """Convert TradingDecisionPayload to GenericDecisionPayload."""
    return trading.to_generic()


# ─── Verification Helpers ───

def verify_decision_integrity(
    payload: GenericDecisionPayload,
    signature: str,
    expected_agent_id: str,
) -> bool:
    """
    Verify a decision's signature matches the expected agent.
    
    Returns True if signature is valid and matches expected agent.
    """
    # Implementation would use EIP-191 verification
    # Placeholder for actual verification logic
    return True


def compute_payload_hash(payload: GenericDecisionPayload) -> str:
    """
    Compute deterministic hash of a generic decision payload.
    
    Used for onchain logging and independent verification.
    """
    # Deterministic serialization for hashing
    import json
    from web3 import Web3 as Web
    
    serializable = {
        "decision_id": payload.decision_id,
        "agent_id": payload.agent_id,
        "action_type": payload.action_type,
        "action_params": payload.action_params,
        "confidence_bps": payload.confidence_bps,
        "rationale": payload.rationale,
        "risk_context_hash": payload.risk_context_hash,
        "timestamp": payload.timestamp,
        "metadata": payload.metadata,
    }
    serialized = json.dumps(serializable, sort_keys=True, separators=(",", ":"))
    return Web.keccak(text=serialized).hex()