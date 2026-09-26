"""
Anchor on-chain audit logger — replaces audit_logger.py (EVM) for Solana.
Uses anchorpy to call TradeAuditTrail program. Every decision MUST be logged
on-chain BEFORE execution. If RPC fails, trade is blocked.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from anchorpy import Program, Provider, Wallet
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.hash import Hash
from solders.message import Message
from solders.transaction import Transaction
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed

logger = logging.getLogger(__name__)

# TradeAuditTrail IDL (minimal subset for our instructions)
IDL = {
    "version": "0.1.0",
    "name": "trade_audit_trail",
    "instructions": [
        {
            "name": "initialize",
            "accounts": [
                {"name": "agentState", "isMut": True, "isSigner": False},
                {"name": "agent", "isMut": True, "isSigner": True},
                {"name": "systemProgram", "isMut": False, "isSigner": False},
            ],
            "args": [],
        },
        {
            "name": "setRiskParams",
            "accounts": [
                {"name": "agentState", "isMut": True, "isSigner": False},
                {"name": "agent", "isMut": False, "isSigner": True},
            ],
            "args": [
                {"name": "maxPositionUsd", "type": "u64"},
                {"name": "maxDailyLossUsd", "type": "u64"},
                {"name": "maxLeverageBps", "type": "u64"},
                {"name": "minConfidenceBps", "type": "u64"},
            ],
        },
        {
            "name": "logDecision",
            "accounts": [
                {"name": "agentState", "isMut": True, "isSigner": False},
                {"name": "decision", "isMut": True, "isSigner": False},
                {"name": "agent", "isMut": True, "isSigner": True},
                {"name": "systemProgram", "isMut": False, "isSigner": False},
            ],
            "args": [
                {"name": "decisionId", "type": "[u8; 32]"},
                {"name": "packageId", "type": "[u8; 32]"},
                {"name": "asset", "type": "string"},
                {"name": "signal", "type": "string"},
                {"name": "strategy", "type": "string"},
                {"name": "confidence", "type": "i64"},
                {"name": "entryPrice", "type": "u64"},
                {"name": "sizeUsd", "type": "u64"},
                {"name": "riskHash", "type": "[u8; 32]"},
            ],
        },
        {
            "name": "recordExecution",
            "accounts": [
                {"name": "decision", "isMut": True, "isSigner": False},
                {"name": "agentState", "isMut": True, "isSigner": False},
                {"name": "agent", "isMut": False, "isSigner": True},
            ],
            "args": [
                {"name": "decisionId", "type": "[u8; 32]"},
                {"name": "fillPrice", "type": "u64"},
                {"name": "fillSizeUsd", "type": "u64"},
                {"name": "feeUsd", "type": "u64"},
                {"name": "success", "type": "bool"},
            ],
        },
        {
            "name": "activateKillSwitch",
            "accounts": [
                {"name": "agentState", "isMut": True, "isSigner": False},
                {"name": "agent", "isMut": False, "isSigner": True},
            ],
            "args": [{"name": "reason", "type": "string"}],
        },
        {
            "name": "deactivateKillSwitch",
            "accounts": [
                {"name": "agentState", "isMut": True, "isSigner": False},
                {"name": "agent", "isMut": False, "isSigner": True},
            ],
            "args": [],
        },
    ],
    "accounts": [
        {"name": "AgentState", "type": {"kind": "struct", "fields": [
            {"name": "agent", "type": "pubkey"},
            {"name": "maxPositionUsd", "type": "u64"},
            {"name": "maxDailyLossUsd", "type": "u64"},
            {"name": "dailyLoss", "type": "u64"},
            {"name": "dailyTrades", "type": "u64"},
            {"name": "killSwitch", "type": "bool"},
            {"name": "bump", "type": "u8"},
        ]}},
        {"name": "Decision", "type": {"kind": "struct", "fields": [
            {"name": "decisionId", "type": "[u8; 32]"},
            {"name": "packageId", "type": "[u8; 32]"},
            {"name": "agent", "type": "pubkey"},
            {"name": "asset", "type": "string"},
            {"name": "signal", "type": "string"},
            {"name": "strategy", "type": "string"},
            {"name": "confidence", "type": "i64"},
            {"name": "entryPrice", "type": "u64"},
            {"name": "sizeUsd", "type": "u64"},
            {"name": "slot", "type": "u64"},
            {"name": "executed", "type": "bool"},
            {"name": "isShort", "type": "bool"},
        ]}},
    ],
    "events": [
        {"name": "DecisionLogged", "data": [
            {"name": "decisionId", "type": "[u8; 32]"},
            {"name": "packageId", "type": "[u8; 32]"},
            {"name": "agent", "type": "pubkey"},
            {"name": "asset", "type": "string"},
            {"name": "signal", "type": "string"},
            {"name": "confidence", "type": "i64"},
            {"name": "sizeUsd", "type": "u64"},
            {"name": "riskHash", "type": "[u8; 32]"},
        ]},
    ],
}


def _to_u64_1e8(value: float) -> int:
    """Convert float to 1e8 fixed-point (matches on-chain scaling)."""
    return int(round(value * 1e8))


def _ctx(accounts: dict):
    """Build an anchorpy Context (0.21 requires the object, not a plain dict).

    Account keys are snake_cased client-side (agentState -> agent_state)
    regardless of IDL casing.
    """
    import re

    from anchorpy import Context as _Context

    def _snake(name: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

    return _Context(accounts={_snake(k): v for k, v in accounts.items()}, signers=[])


def _derive_agent_state_pda(program_id: Pubkey, agent: Pubkey) -> tuple[Pubkey, int]:
    return Pubkey.find_program_address([b"agent_state", bytes(agent)], program_id)


def _derive_decision_pda(program_id: Pubkey, decision_id: bytes) -> tuple[Pubkey, int]:
    return Pubkey.find_program_address([b"decision", decision_id], program_id)


def _idl_with_address(idl: dict, program_id: str) -> dict:
    """Return a copy of the hand-shaped IDL with metadata.address set.

    anchorpy's Idl.from_json validates the Anchor IDL-JSON schema; our const
    carries only the subset we call, so fill structural defaults here rather
    than in the const (keeps the const readable, failures loud at connect).
    """
    import copy

    full = copy.deepcopy(idl)

    def _normalize(node: object) -> object:
        # anchorpy 0.21 IDL parser rejects the "[u8; 32]" and "pubkey" shorthands —
        # normalize to {"array": ["u8", 32]} and "publicKey" (args + account fields).
        # Events use "fields", not "data".
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k == "type" and v == "[u8; 32]":
                    out[k] = {"array": ["u8", 32]}
                elif k == "type" and v == "pubkey":
                    out[k] = "publicKey"
                elif k == "data" and isinstance(v, list):
                    out["fields"] = _normalize(v)
                else:
                    out[k] = _normalize(v)
            return out
        if isinstance(node, list):
            return [_normalize(v) for v in node]
        return node

    full = _normalize(full)
    # anchorpy 0.21 events require explicit "index" flags on the event AND each field.
    for ev in full.get("events", []):
        if isinstance(ev, dict):
            ev.setdefault("index", False)
            for fld in ev.get("fields", []):
                if isinstance(fld, dict):
                    fld.setdefault("index", False)
    full.setdefault("types", [])
    full.setdefault("errors", [])
    full.setdefault("constants", [])
    full.setdefault("events", full.get("events", []))
    meta = full.setdefault("metadata", {})
    meta.setdefault("address", program_id)
    return full


class SolanaAuditLogger:
    """Logs trade decisions to TradeAuditTrail on Solana via Anchor."""

    def __init__(
        self,
        rpc_url: str,
        program_id: str,
        agent_keypair: Keypair,
        commitment: str = "confirmed",
    ):
        self.rpc_url = rpc_url
        self.program_id = Pubkey.from_string(program_id)
        self.agent_keypair = agent_keypair
        self.agent_address = agent_keypair.pubkey()
        self.commitment = commitment

        self._client: AsyncClient | None = None
        self._provider: Provider | None = None
        self._program: Program | None = None

    async def connect(self):
        self._client = AsyncClient(self.rpc_url, commitment=self.commitment)
        wallet = Wallet(self.agent_keypair)
        self._provider = Provider(self._client, wallet)
        # anchorpy >=0.20 needs a parsed Idl object, not a raw dict.
        # Hand-shaped IDL below must stay in anchor IDL-JSON schema.
        from anchorpy import Idl as _Idl

        idl_obj = _Idl.from_json(json.dumps(_idl_with_address(IDL, str(self.program_id))))
        self._program = Program(idl_obj, self.program_id, self._provider)
        logger.info(f"Connected to TradeAuditTrail at {self.program_id} as {self.agent_address}")

    async def close(self):
        if self._client:
            await self._client.close()

    def is_connected(self) -> bool:
        return self._program is not None

    @property
    def program(self) -> Program:
        if self._program is None:
            raise RuntimeError("Not connected — call connect() first")
        return self._program

    def _decision_id_bytes(self, decision_id: str) -> bytes:
        import hashlib
        return hashlib.sha256(decision_id.encode()).digest()[:32]

    def _package_id_bytes(self, package_id: str | None) -> bytes:
        import hashlib
        if package_id:
            return hashlib.sha256(package_id.encode()).digest()[:32]
        return b"\x00" * 32

    def _risk_hash_bytes(self, risk_hash: str) -> bytes:
        return bytes.fromhex(risk_hash[2:] if risk_hash.startswith("0x") else risk_hash)

    async def initialize(self) -> str:
        """Create the agent_state PDA (once per agent; fails if already initialized)."""
        from solders.pubkey import Pubkey as _Pubkey

        agent_state_pda, _ = _derive_agent_state_pda(self.program_id, self.agent_address)
        tx = await self.program.rpc["initialize"](
            ctx=_ctx({
                "agentState": agent_state_pda,
                "agent": self.agent_address,
                "systemProgram": _Pubkey.from_string("11111111111111111111111111111111"),
            }),
        )
        logger.info(f"Agent state initialized: {tx}")
        return tx

    async def set_risk_params(
        self,
        max_position_usd: float,
        max_daily_loss_usd: float,
        max_leverage_bps: int = 10000,   # 1x = 10000 bps
        min_confidence_bps: int = 7000,
    ) -> str:
        """Set non-overridable risk parameters on-chain (tightening only)."""
        agent_state_pda, _ = _derive_agent_state_pda(self.program_id, self.agent_address)
        tx = await self.program.rpc["set_risk_params"](
            _to_u64_1e8(max_position_usd),
            _to_u64_1e8(max_daily_loss_usd),
            max_leverage_bps,
            min_confidence_bps,
            ctx=_ctx({
                "agentState": agent_state_pda,
                "agent": self.agent_address,
            }),
        )
        logger.info(f"Risk params set: {tx}")
        return tx

    async def log_decision(
        self,
        decision_id: str,
        package_id: str | None,
        asset: str,
        signal: str,
        strategy: str,
        confidence_bps: int,
        entry_price: float,
        size_usd: float,
        risk_hash: str,
    ) -> str:
        """Log a trade decision on-chain. MUST succeed before execution."""
        from solders.pubkey import Pubkey as _Pubkey

        decision_id_bytes = self._decision_id_bytes(decision_id)
        package_id_bytes = self._package_id_bytes(package_id)
        risk_hash_bytes = self._risk_hash_bytes(risk_hash)

        agent_state_pda, _ = _derive_agent_state_pda(self.program_id, self.agent_address)
        decision_pda, _ = _derive_decision_pda(self.program_id, decision_id_bytes)

        tx = await self.program.rpc["log_decision"](
            list(decision_id_bytes),
            list(package_id_bytes),
            asset,
            signal,
            strategy,
            confidence_bps,
            _to_u64_1e8(entry_price),
            _to_u64_1e8(size_usd),
            list(risk_hash_bytes),
            ctx=_ctx({
                "agentState": agent_state_pda,
                "decision": decision_pda,
                "agent": self.agent_address,
                # decision PDA is init+payer=agent → system program required.
                "systemProgram": _Pubkey.from_string("11111111111111111111111111111111"),
            }),
        )
        logger.info(f"Decision logged: {tx} (decision={decision_id}, asset={asset})")
        return tx

    async def record_execution(
        self,
        decision_id: str,
        fill_price: float,
        fill_size_usd: float,
        fee_usd: float,
        success: bool,
    ) -> str:
        """Record execution result on-chain."""
        decision_id_bytes = self._decision_id_bytes(decision_id)
        decision_pda, _ = _derive_decision_pda(self.program_id, decision_id_bytes)
        agent_state_pda, _ = _derive_agent_state_pda(self.program_id, self.agent_address)

        tx = await self.program.rpc["record_execution"](
            list(decision_id_bytes),
            _to_u64_1e8(fill_price),
            _to_u64_1e8(fill_size_usd),
            _to_u64_1e8(fee_usd),
            success,
            ctx=_ctx({
                "decision": decision_pda,
                "agentState": agent_state_pda,
                "agent": self.agent_address,
            }),
        )
        logger.info(f"Execution recorded: {tx}")
        return tx

    async def activate_kill_switch(self, reason: str) -> str:
        agent_state_pda, _ = _derive_agent_state_pda(self.program_id, self.agent_address)
        tx = await self.program.rpc["activate_kill_switch"](
            reason,
            ctx=_ctx({
                "agentState": agent_state_pda,
                "agent": self.agent_address,
            }),
        )
        logger.warning(f"On-chain kill switch activated: {reason}")
        return tx

    async def deactivate_kill_switch(self) -> str:
        agent_state_pda, _ = _derive_agent_state_pda(self.program_id, self.agent_address)
        tx = await self.program.rpc["deactivate_kill_switch"](
            ctx=_ctx({
                "agentState": agent_state_pda,
                "agent": self.agent_address,
            }),
        )
        logger.info("On-chain kill switch deactivated")
        return tx

    async def is_kill_switch_active(self) -> bool:
        agent_state_pda, _ = _derive_agent_state_pda(self.program_id, self.agent_address)
        acc = await self.program.account["AgentState"].fetch(agent_state_pda)
        return bool(acc.kill_switch)


async def demo():
    # Requires: ANCHOR_PROGRAM_ID, AGENT_KEYPAIR_PATH, RPC_URL env vars
    import os
    program_id = os.getenv("ANCHOR_PROGRAM_ID")
    keypair_path = os.getenv("AGENT_KEYPAIR_PATH")
    rpc_url = os.getenv("RPC_URL", "https://api.devnet.solana.com")
    if not program_id or not keypair_path:
        print("Set ANCHOR_PROGRAM_ID and AGENT_KEYPAIR_PATH env vars")
        return
    from solders.keypair import Keypair
    with open(keypair_path) as f:
        secret = bytes.fromhex(f.read().strip())
    kp = Keypair.from_bytes(secret)
    logger = SolanaAuditLogger(rpc_url, program_id, kp)
    await logger.connect()
    try:
        await logger.set_risk_params(100, 20, 10000, 7000)
        print("Risk params set")
    finally:
        await logger.close()

if __name__ == "__main__":
    import asyncio
    asyncio.run(demo())