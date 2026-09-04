"""
Onchain audit logger for the autonomous trading agent.
Logs every trade decision to the TradeAuditTrail.sol contract on X Layer
BEFORE the order is submitted to OKX. Creates an immutable, verifiable audit trail.

The contract enforces:
  1. Risk parameters are set before any trading
  2. Position size and daily loss limits are never exceeded
  3. Confidence threshold is always met
  4. Every decision has a valid agent signature (EIP-191 personal_sign)

If the contract rejects, the trade is blocked — the agent cannot bypass it.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any
from pathlib import Path

from web3 import Web3 as Web
from eth_account import Account

from .governance import canonical_decision_hash

logger = logging.getLogger(__name__)

# Gas safety bounds. estimate_gas is capped so a pathological revert estimate
# can't blow the tx through the block gas limit; the floor keeps us off a
# 0-gasPrice tx that no validator would mine.
_GAS_LIMIT_CAP = 1_500_000
_GAS_PRICE_GWEI_FLOOR = 1
_GAS_PRICE_BUFFER = 1.2  # 20% priority buffer over node-reported price


def _to_fixed_point_1e8(value: float) -> int:
    """Convert a float to 1e8 fixed-point using Decimal for exact conversion.

    This avoids binary-float representation errors (e.g. int(0.29 * 1e8) ==
    28999999 instead of 29000000). The Decimal conversion is exact for
    decimal-string inputs, matching how an independent verifier would
    recompute from the JSON payload.
    """
    return int(Decimal(str(value)).scaleb(8).to_integral_value(rounding=ROUND_DOWN))


@dataclass
class DecisionPayload:
    """The data that gets logged to the blockchain before execution."""
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
    # Id shared by every leg of an atomic multi-leg package. A single-leg
    # (non-package) decision leaves it None, which hashes to bytes32(0).
    package_id: str | None = None


# Compiled ABI from TradeAuditTrail.sol (via-ir)
_ABI = [
    {
        "inputs": [
            {"name": "_maxPositionSizeUsd", "type": "uint256"},
            {"name": "_maxDailyLossUsd", "type": "uint256"},
            {"name": "_maxLeverageBps", "type": "uint256"},
            {"name": "_minConfidenceBps", "type": "uint256"},
        ],
        "name": "setRiskParams",
        "outputs": [],
        "type": "function",
    },
    {
        "inputs": [
            {
                "name": "input",
                "type": "tuple",
                "components": [
                    {"name": "decisionId", "type": "bytes32"},
                    {"name": "packageId", "type": "bytes32"},
                    {"name": "asset", "type": "string"},
                    {"name": "signal", "type": "string"},
                    {"name": "strategy", "type": "string"},
                    {"name": "confidence", "type": "int256"},
                    {"name": "entryPrice", "type": "uint256"},
                    {"name": "sizeUsd", "type": "uint256"},
                    {"name": "riskHash", "type": "bytes32"},
                    {"name": "signature", "type": "bytes"},
                    {"name": "isShort", "type": "bool"},
                ],
            },
        ],
        "name": "logDecision",
        "outputs": [],
        "type": "function",
    },
    {
        "inputs": [{"name": "reason", "type": "string"}],
        "name": "activateKillSwitch",
        "outputs": [],
        "type": "function",
    },
    {
        "inputs": [],
        "name": "deactivateKillSwitch",
        "outputs": [],
        "type": "function",
    },
    {
        "inputs": [{"name": "", "type": "address"}],
        "name": "killSwitchActive",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "inputs": [
            {"name": "decisionId", "type": "bytes32"},
            {"name": "fillPrice", "type": "uint256"},
            {"name": "fillSizeUsd", "type": "uint256"},
            {"name": "feeUsd", "type": "uint256"},
            {"name": "success", "type": "bool"},
        ],
        "name": "recordExecution",
        "outputs": [],
        "type": "function",
    },
    {
        "inputs": [{"name": "agent", "type": "address"}],
        "name": "agentRiskParams",
        "outputs": [
            {"name": "maxPositionSizeUsd", "type": "uint256"},
            {"name": "maxDailyLossUsd", "type": "uint256"},
            {"name": "maxLeverageBps", "type": "uint256"},
            {"name": "minConfidenceBps", "type": "uint256"},
        ],
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
            {"name": "confidence", "type": "int256"},
            {"name": "sizeUsd", "type": "uint256"},
            {"name": "riskHash", "type": "bytes32"},
        ],
        "name": "DecisionLogged",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "decisionId", "type": "bytes32"},
            {"indexed": True, "name": "agent", "type": "address"},
            {"name": "fillPrice", "type": "uint256"},
            {"name": "fillSizeUsd", "type": "uint256"},
            {"name": "feeUsd", "type": "uint256"},
            {"name": "success", "type": "bool"},
        ],
        "name": "TradeExecuted",
        "type": "event",
    },
]


class OnchainLogger:
    """Logs trade decisions to TradeAuditTrail.sol on X Layer."""

    def __init__(
        self,
        rpc_url: str,
        contract_address: str,
        private_key: str,
        chain_id: int = 1952,
        signer=None,
    ):
        # Endpoint failover (roadmap Phase 1): the explicit primary first, then
        # any independently configured fallback. First endpoint that answers
        # wins; none answering raises.
        from .rpc import rpc_urls

        self.rpc_urls: list[str] = [rpc_url]
        for url in rpc_urls():
            if url not in self.rpc_urls:
                self.rpc_urls.append(url)

        # Probe via a local so the attribute is inferred as Web3 (not
        # Web3 | None) once assigned — every method below uses it bare.
        w3: Web | None = None
        for url in self.rpc_urls:
            candidate = Web(Web.HTTPProvider(url))
            try:
                if candidate.is_connected():
                    w3 = candidate
                    if url != self.rpc_urls[0]:
                        logger.warning(f"Primary RPC unreachable; connected via fallback {url}")
                    break
            except Exception as e:
                logger.warning(f"RPC endpoint {url} failed probe: {e}")
        if w3 is None:
            raise ConnectionError(
                f"Cannot connect to any X Layer RPC endpoint ({', '.join(self.rpc_urls)})"
            )
        self.w3 = w3
        self.contract_address = Web.to_checksum_address(contract_address)
        # D10/ika-CAPTURE-1: attestation signing goes through the backend so
        # custodial -> connect-wallet is a swap, not a rewrite. private_key
        # and account stay for transaction broadcast + backward compat
        # (tests construct loggers manually); the signer is the authority
        # for what address we attest as.
        from .signer import EnvKeySigner
        self.private_key = private_key
        self.account = Account.from_key(private_key)
        self.signer = signer or EnvKeySigner(private_key)
        self.chain_id = chain_id
        self.agent_address = self.signer.address
        self.contract = self.w3.eth.contract(address=self.contract_address, abi=_ABI)
        # Thread-safe local nonce counter. Seeded lazily from the node on the
        # first send, then incremented locally so concurrent sends (kill
        # switch + logDecision racing from different threads) can't grab the
        # same transaction count and collide.
        self._nonce_lock = threading.Lock()
        self._nonce_counter: int | None = None
        # Measure actual block time once at construction (D6-3a).
        # Replaces the unverified 15s/5760 blocks-per-day assumption with a
        # measured value for day-window math (durable counters, volume caps).
        self._blocks_per_day: int = self._measure_blocks_per_day()

    def _measure_blocks_per_day(self) -> int:
        """Measure actual blocks per day from the chain.

        Samples the latest block and a block ~24 hours ago (by timestamp)
        to compute the real blocks-per-day rate. Falls back to 5760 (15s
        blocks) if measurement fails.
        """
        try:
            latest = self.w3.eth.get_block("latest")
            latest_ts = latest.timestamp  # type: ignore[attr-defined]
            latest_num = latest.number  # type: ignore[attr-defined]
            # Target timestamp: 24 hours ago
            target_ts = latest_ts - 86400
            # Binary search for block at target timestamp (rough approximation)
            # Start with estimated 15s blocks = 5760 blocks per day
            est_blocks_per_day = 5760
            low = max(0, latest_num - est_blocks_per_day * 2)
            high = latest_num
            while low <= high:
                mid = (low + high) // 2
                block = self.w3.eth.get_block(mid)
                if block.timestamp < target_ts:  # type: ignore[attr-defined]
                    low = mid + 1
                else:
                    high = mid - 1
            # high is the last block with timestamp >= target_ts
            if high >= 0:
                day_start_block = self.w3.eth.get_block(high)
                measured = latest_num - day_start_block.number  # type: ignore[attr-defined]
                if 1000 <= measured <= 20000:  # sanity bounds
                    logger.info(f"Measured blocks per day: {measured}")
                    return measured
        except Exception as e:
            logger.warning(f"Block time measurement failed, using fallback 5760: {e}")
        return 5760

    def is_connected(self) -> bool:
        return self.w3.is_connected()

    def _failover(self) -> bool:
        """Switch self.w3 to the next configured endpoint. True on success.

        Called only from the send path under the nonce lock, so swapping the
        provider mid-flight cannot race a concurrent send.

        On successful failover, re-reads the chain nonce and warns if it
        diverges from the local counter (D6-2a).
        """
        current_url = getattr(self.w3.provider, "endpoint_uri", None)
        for url in self.rpc_urls:
            if url == current_url:
                continue
            candidate = Web(Web.HTTPProvider(url))
            try:
                if candidate.is_connected():
                    logger.warning(f"Failing over X Layer RPC to {url}")
                    # Re-read nonce from the new endpoint and check for divergence
                    old_counter = self._nonce_counter
                    self.w3 = candidate
                    new_nonce = self.w3.eth.get_transaction_count(self.agent_address)
                    if old_counter is not None and new_nonce != old_counter:
                        logger.warning(
                            f"Nonce divergence on failover: local counter={old_counter}, "
                            f"chain nonce={new_nonce}. Resyncing local counter."
                        )
                    self._nonce_counter = new_nonce
                    return True
            except Exception as e:
                logger.warning(f"Failover probe failed for {url}: {e}")
        return False

    def get_nonce(self) -> int:
        """Node-reported transaction count. Kept for scripts/external callers;
        the transaction path uses _next_nonce() for race safety."""
        return self.w3.eth.get_transaction_count(self.agent_address)

    def _next_nonce(self) -> int:
        """Return the next nonce to use, maintaining a local monotonic counter
        so parallel sends don't collide. The counter is seeded from the node on
        first use and bumped once per send."""
        if self._nonce_counter is None:
            self._nonce_counter = self.w3.eth.get_transaction_count(self.agent_address)
        else:
            self._nonce_counter += 1
        return self._nonce_counter

    def _estimate_gas(self, func: Any) -> int:
        """Estimate gas for a contract function call, capped at _GAS_LIMIT_CAP.
        Falls back to a fixed budget if estimation reverts (e.g. a would-be
        rejected call) so we still broadcast and learn the real reason on-chain."""
        try:
            est = func.estimate_gas({"from": self.agent_address})
            return min(int(est * 1.25), _GAS_LIMIT_CAP)
        except Exception as e:
            logger.warning(f"Gas estimation failed ({e}); using fallback 300000")
            return 300_000

    def _gas_price(self) -> int:
        """Use the node-reported gas price with a 20% priority buffer and a 1
        gwei floor, rather than a hardcoded 1 gwei that X Layer may reject."""
        try:
            gp = self.w3.eth.generate_gas_price()
            if not gp:
                gp = self.w3.to_wei(_GAS_PRICE_GWEI_FLOOR, "gwei")
            return max(int(gp * _GAS_PRICE_BUFFER), self.w3.to_wei(_GAS_PRICE_GWEI_FLOOR, "gwei"))
        except Exception:
            return self.w3.to_wei(_GAS_PRICE_GWEI_FLOOR, "gwei")

    def _send_transaction(self, build_fn: Any, label: str) -> str:
        """Build, sign, send and await a contract tx with safe gas/nonce.

        All onchain writes go through here so gas estimation, dynamic gas
        price, and the thread-safe nonce counter are applied uniformly. The
        nonce is reserved under a lock so concurrent sends serialize their
        nonces; the receipt wait happens outside the lock. A reverted receipt
        raises instead of being silently treated as success.
        """
        with self._nonce_lock:
            nonce = self._next_nonce()
            func = build_fn()
            gas = self._estimate_gas(func)
            gas_price = self._gas_price()
            tx_data = func.build_transaction({
                "chainId": self.chain_id,
                "nonce": nonce,
                "gas": gas,
                "gasPrice": gas_price,
            })
            signed = self.w3.eth.account.sign_transaction(tx_data, self.private_key)
            try:
                tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            except (ConnectionError, OSError) as e:
                # Broadcast-level failure: the endpoint refused the connection
                # itself. Fail over and retry ONCE — but only after checking the
                # surviving node for the signed hash, because a lost response is
                # not proof the tx never landed. Unknown state => never resend.
                if not self._failover():
                    raise
                try:
                    tx_hex = signed.hash.hex()
                    if not tx_hex.startswith("0x"):
                        tx_hex = "0x" + tx_hex
                    prior = self.w3.eth.get_transaction_receipt(tx_hex)
                    logger.warning(f"{label}: tx already mined on fallback node ({tx_hex})")
                    tx_hash = prior["transactionHash"]
                except Exception:
                    self._nonce_counter = None  # reseed from the surviving node
                    nonce = self._next_nonce()
                    tx_data = func.build_transaction({
                        "chainId": self.chain_id,
                        "nonce": nonce,
                        "gas": gas,
                        "gasPrice": gas_price,
                    })
                    signed = self.w3.eth.account.sign_transaction(tx_data, self.private_key)
                    tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt.get("status") != 1:
            raise RuntimeError(f"{label} reverted on-chain (tx {tx_hash.hex()})")
        logger.info(f"{label}. Tx: {tx_hash.hex()}")
        return tx_hash.hex()

    def set_risk_params(
        self,
        max_position_usd: float,
        max_daily_loss_usd: float,
        max_leverage_bps: int = 50000,
        min_confidence_bps: int = 7000,
    ) -> str:
        """Set non-overridable risk parameters on the contract.

        Units, precisely (this is where the live contract previously broke):
        - max_position_usd / max_daily_loss_usd: plain USD, scaled to 1e8
          fixed-point below to match how sizeUsd/entryPrice are scaled when
          logging decisions (audit_logger.py, ~line 275).
        - max_leverage_bps: basis points where 10000 = 1x (100%). The
          previous default here was 500, i.e. 0.05x — with position size
          correctly scaled to $5000 * 1e8, that capped every real order at
          maxAllowed = (5000e8 * 500) / 10000 = $250, so the on-chain gate
          silently rejected every realistic trade. 5x leverage is 50000 bps,
          not 500. Verify with TradeAuditTrail.sol's own leverage check
          (search for EXCEEDS_MAX_LEVERAGE) before changing this default.
        """
        def build():
            return self.contract.functions.setRiskParams(
                int(max_position_usd * 1e8),
                int(max_daily_loss_usd * 1e8),
                max_leverage_bps,
                min_confidence_bps,
            )
        return self._send_transaction(build, "Risk params set")

    def _compute_payload_hash(self, payload: DecisionPayload) -> bytes:
        """Compute the keccak256 hash of the decision payload (what the agent signs).

        Thin wrapper over governance.canonical_decision_hash — the single
        source of truth for the TradeAuditTrail.sol logDecision layout
        (S8). The agent signs this hash with personal_sign (EIP-191), which
        prepends "\x19Ethereum Signed Message:\n32" + hash.
        """
        return canonical_decision_hash(
            decision_id=payload.decision_id,
            package_id=payload.package_id,
            agent_address=payload.agent_address,
            asset=payload.asset,
            signal=payload.signal,
            strategy=payload.strategy,
            confidence_bps=payload.confidence_bps,
            entry_price=payload.entry_price,
            size_usd=payload.size_usd,
            risk_hash=payload.risk_params_hash,
        )

    def _sign_payload(self, payload: DecisionPayload) -> bytes:
        """Sign the payload hash using EIP-191 (personal_sign).

        The contract uses ecrecover with the Ethereum signed message prefix:
        keccak256("\x19Ethereum Signed Message:\n32" + payload_hash)

        So we must use personal_sign, not EIP-712. Previously this called
        self.account.sign_message(payload_hash, mechanism="personal") —
        that isn't a valid signature on eth_account's LocalAccount.sign_message
        (no such "mechanism" kwarg exists), so this raised TypeError on
        every call. It never reached the network; the on-chain audit trail
        couldn't run at all. Fixed by building the EIP-191 SignableMessage
        with encode_defunct, which is the actual API for this.

        Signs through the pluggable backend (D10): the digest is the single
        canonical hash (S8) either way — swapping custodial for a session
        backend changes WHO signs, never WHAT bytes get signed.
        """
        payload_hash = self._compute_payload_hash(payload)
        return self._get_signer().sign_message_digest(payload_hash)

    def _get_signer(self):
        """Return the attestation backend, deriving it if necessary.

        Production loggers get theirs in __init__. Manually-constructed
        loggers (tests via __new__) carry `account` but no signer — derive
        the custodial backend from the SAME key so signatures are identical.
        """
        signer = getattr(self, "signer", None)
        if signer is not None:
            return signer
        from .signer import EnvKeySigner
        return EnvKeySigner(self.account.key.hex())

    def log_decision(self, payload: DecisionPayload) -> str:
        """Log a trade decision to the blockchain. Returns tx hash.

        This is the hard gate — must be called BEFORE any order placement.
        If the contract reverts, the trade is blocked.
        """
        risk_hash_bytes = bytes.fromhex(
            payload.risk_params_hash[2:] if payload.risk_params_hash.startswith("0x")
            else payload.risk_params_hash
        )
        signature = self._sign_payload(payload)
        decision_id_hash = Web.keccak(text=payload.decision_id)
        package_id_hash = (
            Web.keccak(text=payload.package_id)
            if payload.package_id
            else b"\x00" * 32
        )

        # Build struct for logDecision
        decision_input = {
            "decisionId": decision_id_hash,
            "packageId": package_id_hash,
            "asset": payload.asset,
            "signal": payload.signal,
            "strategy": payload.strategy,
            "confidence": payload.confidence_bps,
            "entryPrice": _to_fixed_point_1e8(payload.entry_price),
            "sizeUsd": _to_fixed_point_1e8(payload.size_usd),
            "riskHash": risk_hash_bytes,
            "signature": signature,
            "isShort": payload.is_short,
        }

        def build():
            return self.contract.functions.logDecision(decision_input)
        return self._send_transaction(build, "Decision logged")

    def record_execution(
        self,
        decision_id: str,
        fill_price: float,
        fill_size_usd: float,
        fee_usd: float,
        success: bool,
    ) -> str:
        """Record execution result. Must reference a previously logged decision."""
        decision_id_hash = Web.keccak(text=decision_id)

        def build():
            return self.contract.functions.recordExecution(
                decision_id_hash,
                _to_fixed_point_1e8(fill_price),
                _to_fixed_point_1e8(fill_size_usd),
                _to_fixed_point_1e8(fee_usd),
                success,
            )
        return self._send_transaction(build, "Execution recorded")

    def activate_kill_switch(self, reason: str) -> str:
        """Halt all onchain logDecision calls from this agent. Mirrors
        RiskGate.activate_kill_switch — call both so the halt is enforced
        even if only one layer is checked by a given caller."""
        def build():
            return self.contract.functions.activateKillSwitch(reason)
        return self._send_transaction(build, f"Onchain kill switch activated: {reason}")

    def deactivate_kill_switch(self) -> str:
        """Resume onchain trading. A deliberate, separate call."""
        def build():
            return self.contract.functions.deactivateKillSwitch()
        return self._send_transaction(build, "Onchain kill switch deactivated")

    def is_kill_switch_active(self) -> bool:
        return bool(self.contract.functions.killSwitchActive(self.agent_address).call())

    def get_decision(self, decision_id: str) -> dict:
        """Query a decision from the contract by ID."""
        decision_id_hash = Web.keccak(text=decision_id)
        try:
            events = self.contract.events.DecisionLogged.get_logs(from_block=0)
            for evt in events:
                if evt["args"].get("decisionId") == decision_id_hash:
                    return dict(evt["args"])
        except Exception as e:
            logger.warning(f"Failed to query decision: {e}")
        return {}

    def compute_risk_hash(self, params: dict) -> str:
        """Compute a deterministic hash of risk parameters for logging."""
        serialized = json.dumps(params, sort_keys=True)
        return Web.keccak(text=serialized).hex()

    def get_contract_stats(self, days: int = 7) -> dict:
        """Query onchain decisions and executions from the past N days."""
        from_block = max(0, self.w3.eth.block_number - days * self._blocks_per_day)
        decisions = []

        try:
            decision_events = self.contract.events.DecisionLogged.get_logs(
                from_block=from_block
            )
            for evt in decision_events:
                args = dict(evt["args"])
                args.pop("indexing", None)  # remove non-serializable 'indexed' key
                decisions.append(args)
        except Exception as e:
            logger.warning(f"Failed to query decisions: {e}")

        return {
            "decisions": decisions,
            "num_decisions": len(decisions),
            "from_block": from_block,
            "current_block": self.w3.eth.block_number,
            "agent_address": self.agent_address,
            "blocks_per_day": self._blocks_per_day,
        }
