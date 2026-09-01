"""
Read-only verify SDK for Product A — Audit/Governance Platform.

Python client for independent verification of onchain decision logs.
Usage: `pip install tarstrade-verify` (when published)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional
from pathlib import Path

from web3 import Web3 as Web
from eth_account.messages import encode_defunct
from eth_account import Account

from .governance import GenericDecisionPayload, compute_payload_hash
from dataclasses import dataclass, field


@dataclass
class VerificationResult:
    """Result of verifying a decision's integrity."""
    decision_id: str
    valid: bool
    payload: dict
    signature_valid: bool
    agent_verified: bool
    tx_confirmed: bool
    block_number: int | None
    errors: list[str] = field(default_factory=list)


@dataclass
class ContractConfig:
    """Configuration for connecting to TradeAuditTrail contract."""
    rpc_url: str
    contract_address: str
    chain_id: int = 1952  # X Layer


class VerifyClient:
    """
    Read-only client for verifying onchain decision logs.
    
    No private keys, no write access — purely for verification.
    """
    
    def __init__(self, config: ContractConfig):
        self.config = config
        self.w3 = Web(Web.HTTPProvider(config.rpc_url))
        self.contract_address = Web.to_checksum_address(config.contract_address)
        self._load_abi()
    
    def _load_abi(self) -> None:
        """Load TradeAuditTrail ABI."""
        abi_path = Path(__file__).parent / "contracts" / "artifacts" / "TradeAuditTrail_abi.json"
        if abi_path.exists():
            with open(abi_path) as f:
                self.abi = json.load(f)
        else:
            # Minimal ABI for verification
            self.abi = [
                {
                    "inputs": [{"name": "decisionId", "type": "bytes32"}],
                    "name": "getDecision",
                    "outputs": [
                        {"name": "decisionId", "type": "bytes32"},
                        {"name": "packageId", "type": "bytes32"},
                        {"name": "agent", "type": "address"},
                        {"name": "asset", "type": "string"},
                        {"name": "signal", "type": "string"},
                        {"name": "strategy", "type": "string"},
                        {"name": "confidence", "type": "int256"},
                        {"name": "entryPrice", "type": "uint256"},
                        {"name": "sizeUsd", "type": "uint256"},
                        {"name": "timestamp", "type": "uint256"},
                        {"name": "riskHash", "type": "bytes32"},
                        {"name": "signature", "type": "bytes"},
                        {"name": "executed", "type": "bool"},
                        {"name": "isShort", "type": "bool"},
                    ],
                    "stateMutability": "view",
                    "type": "function",
                },
                {
                    "inputs": [{"name": "decisionId", "type": "bytes32"}],
                    "name": "getExecution",
                    "outputs": [
                        {"name": "fillPrice", "type": "uint256"},
                        {"name": "fillSizeUsd", "type": "uint256"},
                        {"name": "feeUsd", "type": "uint256"},
                        {"name": "success", "type": "bool"},
                    ],
                    "stateMutability": "view",
                    "type": "function",
                },
                {
                    "anonymous": False,
                    "inputs": [
                        {"indexed": True, "name": "decisionId", "type": "bytes32"},
                        {"indexed": True, "name": "packageId", "type": "bytes32"},
                        {"indexed": True, "name": "agent", "type": "address"},
                        {"name": "asset", "type": "string"},
                        {"name": "signal", "type": "string"},
                        {"name": "strategy", "type": "string"},
                        {"name": "confidence", "type": "int256"},
                        {"name": "sizeUsd", "type": "uint256"},
                        {"name": "riskHash", "type": "bytes32"},
                    ],
                    "name": "DecisionLogged",
                    "type": "event",
                },
            ]
    
    def is_connected(self) -> bool:
        try:
            return self.w3.is_connected()
        except Exception:
            return False
    
    def get_decision(self, decision_id: str) -> dict | None:
        """
        Fetch a decision from the contract by ID.
        
        Returns None if not found.
        """
        try:
            decision_id_hash = Web.keccak(text=decision_id)
            contract = self.w3.eth.contract(address=self.contract_address, abi=self.abi)
            
            # Try direct call first
            try:
                result = contract.functions.getDecision(decision_id_hash).call()
                return self._parse_decision_result(result, decision_id)
            except Exception:
                # Fallback: search events
                return self._find_decision_in_events(decision_id_hash)
        except Exception as e:
            return {"error": str(e)}
    
    def _parse_decision_result(self, result: tuple, decision_id: str) -> dict:
        """Parse raw contract result into readable dict."""
        # TradeDecision struct field order
        (decision_id_bytes, package_id_bytes, agent, asset, signal, strategy,
         confidence, entry_price, size_usd, timestamp, risk_hash,
         signature, executed, is_short) = result
        
        return {
            "decision_id": decision_id_bytes.hex() if isinstance(decision_id_bytes, bytes) else str(decision_id_bytes),
            "package_id": package_id_bytes.hex() if isinstance(package_id_bytes, bytes) else str(package_id_bytes),
            "agent": agent,
            "asset": asset,
            "signal": signal,
            "strategy": strategy,
            "confidence": confidence,
            "confidence_bps": confidence,
            "entry_price": entry_price / 1e8,
            "size_usd": size_usd / 1e8,
            "timestamp": timestamp,
            "risk_hash": risk_hash.hex() if isinstance(risk_hash, bytes) else str(risk_hash),
            "signature": signature.hex() if isinstance(signature, bytes) else str(signature),
            "executed": executed,
            "is_short": is_short,
        }
    
    def _find_decision_in_events(self, decision_id_hash: bytes) -> dict | None:
        """Search DecisionLogged events for a decision."""
        try:
            contract = self.w3.eth.contract(address=self.contract_address, abi=self.abi)
            events = contract.events.DecisionLogged.get_logs(from_block=0)
            for evt in events:
                if evt["args"].get("decisionId") == decision_id_hash:
                    args = dict(evt["args"])
                    args.pop("indexing", None)
                    return {
                        "decision_id": args.get("decisionId", decision_id_hash).hex() if hasattr(args.get("decisionId"), "hex") else str(args.get("decisionId")),
                        "package_id": args.get("packageId", b"").hex() if hasattr(args.get("packageId"), "hex") else str(args.get("packageId")),
                        "agent": args.get("agent"),
                        "asset": args.get("asset"),
                        "signal": args.get("signal"),
                        "strategy": args.get("strategy"),
                        "confidence": args.get("confidence"),
                        "size_usd": args.get("sizeUsd", 0) / 1e8,
                        "risk_hash": args.get("riskHash", "").hex() if hasattr(args.get("riskHash"), "hex") else str(args.get("riskHash")),
                    }
        except Exception:
            pass
        return None
    
    def get_execution(self, decision_id: str) -> dict | None:
        """Fetch execution record for a decision."""
        try:
            decision_id_hash = Web.keccak(text=decision_id)
            contract = self.w3.eth.contract(address=self.contract_address, abi=self.abi)
            result = contract.functions.getExecution(decision_id_hash).call()
            (fill_price, fill_size_usd, fee_usd, success) = result
            return {
                "fill_price": fill_price / 1e8,
                "fill_size_usd": fill_size_usd / 1e8,
                "fee_usd": fee_usd / 1e8,
                "success": success,
            }
        except Exception:
            return None
    
    def verify_decision(
        self,
        decision_id: str,
        expected_agent: str | None = None,
    ) -> VerificationResult:
        """
        Fully verify a decision's integrity.
        
        Checks:
        1. Decision exists onchain
        2. Signature is valid EIP-191 from expected agent
        3. Transaction is confirmed
        """
        decision = self.get_decision(decision_id)
        if not decision or "error" in decision:
            return VerificationResult(
                decision_id=decision_id,
                valid=False,
                payload={},
                signature_valid=False,
                agent_verified=False,
                tx_confirmed=False,
                block_number=None,
                errors=[decision.get("error", "Decision not found")],
            )
        
        # Verify signature
        from .audit_logger import OnchainLogger
        
        # Reconstruct payload for verification
        payload = GenericDecisionPayload(
            decision_id=decision["decision_id"],
            agent_id=decision["agent"],
            action_type="trade",
            action_params={
                "asset": decision["asset"],
                "signal": decision["signal"],
                "entry_price": decision.get("entry_price", 0),
                "size_usd": decision.get("size_usd", 0),
            },
            confidence_bps=decision.get("confidence_bps", decision.get("confidence", 0)),
            rationale=decision.get("strategy", ""),
            risk_context_hash=decision.get("risk_hash", ""),
            timestamp=decision.get("timestamp", 0),
        )
        
        # Verify EIP-191 signature
        signature_valid = False
        agent_verified = False
        if decision.get("signature"):
            try:
                sig = decision["signature"]
                if isinstance(sig, str) and sig.startswith("0x"):
                    sig = bytes.fromhex(sig[2:])
                elif isinstance(sig, str):
                    sig = bytes.fromhex(sig)
                
                payload_hash = compute_payload_hash(payload)
                signable = encode_defunct(primitive=payload_hash)
                recovered = Account.recover_message(signable, signature=sig)
                signature_valid = True
                agent_verified = (recovered.lower() == decision["agent"].lower())
                if expected_agent:
                    agent_verified = agent_verified and (recovered.lower() == expected_agent.lower())
            except Exception:
                pass
        
        return VerificationResult(
            decision_id=decision_id,
            valid=signature_valid and agent_verified,
            payload=payload.__dict__,
            signature_valid=signature_valid,
            agent_verified=agent_verified,
            tx_confirmed=True,  # If we got it from contract, it's confirmed
            block_number=None,  # Would need event log to get this
            errors=[],
        )
    
    def get_recent_decisions(self, count: int = 20) -> list[dict]:
        """Get recent decisions from contract events."""
        try:
            contract = self.w3.eth.contract(address=self.contract_address, abi=self.abi)
            events = contract.events.DecisionLogged.get_logs(from_block=0)
            decisions = []
            for evt in events[-count:]:
                args = dict(evt["args"])
                args.pop("indexing", None)
                decisions.append({
                    "decision_id": args.get("decisionId", b"").hex() if hasattr(args.get("decisionId"), "hex") else str(args.get("decisionId")),
                    "asset": args.get("asset"),
                    "signal": args.get("signal"),
                    "confidence": args.get("confidence"),
                    "size_usd": args.get("sizeUsd", 0) / 1e8,
                    "timestamp": args.get("timestamp", 0),
                    "executed": args.get("executed", False),
                })
            return decisions
        except Exception:
            return []


# Convenience function for quick verification
def quick_verify(
    rpc_url: str,
    contract_address: str,
    decision_id: str,
    expected_agent: str | None = None,
) -> VerificationResult:
    """One-liner for quick decision verification."""
    config = ContractConfig(rpc_url=rpc_url, contract_address=contract_address)
    client = VerifyClient(config)
    return client.verify_decision(decision_id, expected_agent)


# Example usage
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 4:
        print("Usage: python -m tarstrade.verify <rpc_url> <contract_address> <decision_id> [expected_agent]")
        sys.exit(1)
    
    result = quick_verify(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    print(json.dumps(result.__dict__, indent=2, default=str))