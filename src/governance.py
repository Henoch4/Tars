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

from web3 import Web3 as Web


def _to_fixed_point_1e8(value: float) -> int:
    """Convert a float to 1e8 fixed-point using Decimal for exact conversion."""
    return int(Decimal(str(value)).scaleb(8).to_integral_value(rounding=ROUND_DOWN))


_ZERO_BYTES32 = b"\x00" * 32


def _coerce_bytes32(value: Any, field_name: str, none_as_zero: bool) -> bytes:
    """Coerce a bytes32-ish value to exactly 32 bytes. Fail-closed.

    Accepts: None (→ zero only when the protocol defines it, e.g.
    package_id of a single-leg decision), raw 32-byte values, "0x"-prefixed
    or bare 64-char hex, and preimage strings (→ keccak(text), the signing
    path for decision_id/package_id). Anything else raises ValueError
    instead of packing garbage into a signed digest (S5).
    """
    if value is None:
        if none_as_zero:
            return _ZERO_BYTES32
        raise ValueError(f"{field_name}: missing (None) — refusing to hash")
    if isinstance(value, bytes):
        if len(value) != 32:
            raise ValueError(
                f"{field_name}: {len(value)} bytes, expected 32 — refusing to hash"
            )
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            if none_as_zero:
                return _ZERO_BYTES32
            raise ValueError(f"{field_name}: empty — refusing to hash")
        if s.startswith(("0x", "0X")):
            # A 0x prefix declares hex: non-conforming hex raises instead
            # of silently becoming a preimage (short-hash hole).
            hexpart = s[2:]
            if len(hexpart) != 64:
                raise ValueError(
                    f"{field_name}: 0x-prefixed but {len(hexpart)} hex chars, "
                    "expected 64 — refusing to hash"
                )
            try:
                return bytes.fromhex(hexpart)
            except ValueError:
                raise ValueError(f"{field_name}: 0x-prefixed but not hex")
        if len(s) == 64:
            try:
                return bytes.fromhex(s)
            except ValueError:
                pass  # bare non-hex 64-char string: treat as preimage
        return Web.keccak(text=s)
    raise ValueError(f"{field_name}: unsupported type {type(value).__name__}")


def _coerce_address(value: str) -> bytes:
    """Coerce a 0x-prefixed 20-byte hex address to raw bytes. Fail-closed."""
    if not isinstance(value, str) or not value.startswith(("0x", "0X")):
        raise ValueError(f"agent address must be 0x-prefixed hex, got {value!r}")
    try:
        raw = bytes.fromhex(value[2:])
    except ValueError:
        raise ValueError(f"agent address is not valid hex: {value!r}")
    if len(raw) != 20:
        raise ValueError(f"agent address must be 20 bytes, got {len(raw)}")
    return raw


def canonical_decision_hash(
    decision_id: Any,
    package_id: Any,
    agent_address: str,
    asset: str,
    signal: str,
    strategy: str,
    confidence_bps: int,
    entry_price: float,
    size_usd: float,
    risk_hash: Any,
) -> bytes:
    """Single source of truth for the decision digest (S8).

    Byte-exact layout of TradeAuditTrail.sol logDecision:
    keccak256(abi.encodePacked(decisionId, packageId, agent, asset,
    signal, strategy, confidence(int256), entryPrice(uint256 1e8),
    sizeUsd(uint256 1e8), riskHash)) where decisionId/packageId are
    bytes32 (keccak of the text id, or bytes32(0) for a single leg).

    Both the signing path (audit_logger) and the verification path
    (VerifyClient) must use this — a roundtrip that matches its own
    scheme proves nothing about the deployed contract's scheme.
    """
    packed = (
        _coerce_bytes32(decision_id, "decision_id", none_as_zero=False)
        + _coerce_bytes32(package_id, "package_id", none_as_zero=True)
        + _coerce_address(agent_address)
        + asset.encode("utf-8")
        + signal.encode("utf-8")
        + strategy.encode("utf-8")
        + int(confidence_bps).to_bytes(32, "big", signed=True)
        + _to_fixed_point_1e8(entry_price).to_bytes(32, "big")
        + _to_fixed_point_1e8(size_usd).to_bytes(32, "big")
        + _coerce_bytes32(risk_hash, "risk_hash", none_as_zero=False)
    )
    return Web.keccak(packed)


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
    """Verify a decision's EIP-191 signature recovers the expected agent.

    Uses the canonical digest (same bytes the contract checks), so a True
    here means the exact payload the agent signed — not a self-agreeing
    scheme. Never raises: unparsable input verifies as False (fail-closed).
    """
    from eth_account import Account
    from eth_account.messages import encode_defunct

    try:
        digest = bytes.fromhex(compute_payload_hash(payload).removeprefix("0x"))
        sig = signature[2:] if signature.startswith(("0x", "0X")) else signature
        recovered = Account.recover_message(
            encode_defunct(primitive=digest), signature=bytes.fromhex(sig)
        )
        return recovered.lower() == expected_agent_id.lower()
    except Exception:
        return False


def compute_payload_hash(payload: GenericDecisionPayload) -> str:
    """Digest of a generic decision payload under the canonical scheme.

    Same bytes TradeAuditTrail.sol recomputes in logDecision (see
    canonical_decision_hash). action_params carries asset/signal/
    entry_price/size_usd, rationale carries the strategy, metadata may
    carry package_id (str preimage, hex, or raw bytes32 — else bytes32(0)).
    Returns hex without 0x prefix (unchanged API).
    """
    params = payload.action_params or {}
    metadata = payload.metadata or {}
    return canonical_decision_hash(
        decision_id=payload.decision_id,
        package_id=metadata.get("package_id"),
        agent_address=payload.agent_id,
        asset=params.get("asset", ""),
        signal=params.get("signal", ""),
        strategy=payload.rationale,
        confidence_bps=payload.confidence_bps,
        entry_price=params.get("entry_price", 0),
        size_usd=params.get("size_usd", 0),
        risk_hash=payload.risk_context_hash,
    ).hex()