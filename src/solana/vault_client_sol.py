"""
Anchor vault client — drives TradingVault on Solana via anchorpy.

Mirrors audit_logger_sol.py conventions exactly: hand-shaped IDL const,
parsed Idl, Context object, snake_case accounts. Deposit mints vault shares
(mock USDC doubles as the share mint by contract design).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from anchorpy import Program, Provider, Wallet
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient

from .audit_logger_sol import _ctx, _idl_with_address

logger = logging.getLogger(__name__)

TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
SYSTEM_PROGRAM_ID = Pubkey.from_string("11111111111111111111111111111111")
RENT_SYSVAR_ID = Pubkey.from_string("SysvarRent111111111111111111111111111111111")

# TradingVault IDL (minimal subset for our instructions + Vault account reads)
IDL = {
    "version": "0.1.0",
    "name": "trading_vault",
    "instructions": [
        {
            "name": "initialize",
            "accounts": [
                {"name": "vault", "isMut": True, "isSigner": False},
                {"name": "owner", "isMut": True, "isSigner": True},
                {"name": "agent", "isMut": False, "isSigner": True},
                {"name": "mint", "isMut": False, "isSigner": False},
                {"name": "systemProgram", "isMut": False, "isSigner": False},
                {"name": "rent", "isMut": False, "isSigner": False},
            ],
            "args": [
                {"name": "minDeposit", "type": "u64"},
                {"name": "maxTvl", "type": "u64"},
                {"name": "attestTimelock", "type": "u64"},
                {"name": "maxAttestationDeltaBps", "type": "u64"},
            ],
        },
        {
            "name": "deposit",
            "accounts": [
                {"name": "vault", "isMut": True, "isSigner": False},
                {"name": "user", "isMut": True, "isSigner": True},
                {"name": "userTokenAccount", "isMut": True, "isSigner": False},
                {"name": "vaultTokenAccount", "isMut": True, "isSigner": False},
                {"name": "mint", "isMut": True, "isSigner": False},
                {"name": "tokenProgram", "isMut": False, "isSigner": False},
                {"name": "systemProgram", "isMut": False, "isSigner": False},
            ],
            "args": [{"name": "amount", "type": "u64"}],
        },
        {
            "name": "withdraw",
            "accounts": [
                {"name": "vault", "isMut": True, "isSigner": False},
                {"name": "user", "isMut": True, "isSigner": True},
                {"name": "userTokenAccount", "isMut": True, "isSigner": False},
                {"name": "vaultTokenAccount", "isMut": True, "isSigner": False},
                {"name": "mint", "isMut": True, "isSigner": False},
                {"name": "tokenProgram", "isMut": False, "isSigner": False},
            ],
            "args": [{"name": "shares", "type": "u64"}],
        },
        {
            "name": "attestTotalAssets",
            "accounts": [
                {"name": "vault", "isMut": True, "isSigner": False},
                {"name": "agent", "isMut": False, "isSigner": True},
            ],
            "args": [{"name": "newTotalAssets", "type": "u64"}],
        },
        {
            "name": "setPackageOpen",
            "accounts": [
                {"name": "vault", "isMut": True, "isSigner": False},
                {"name": "agent", "isMut": False, "isSigner": True},
            ],
            "args": [{"name": "open", "type": "bool"}],
        },
    ],
    "accounts": [
        {"name": "Vault", "type": {"kind": "struct", "fields": [
            {"name": "owner", "type": "pubkey"},
            {"name": "agent", "type": "pubkey"},
            {"name": "mint", "type": "pubkey"},
            {"name": "minDeposit", "type": "u64"},
            {"name": "maxTvl", "type": "u64"},
            {"name": "attestTimelock", "type": "u64"},
            {"name": "maxAttestationDeltaBps", "type": "u64"},
            {"name": "totalAssets", "type": "u64"},
            {"name": "totalShares", "type": "u64"},
            {"name": "packageOpen", "type": "bool"},
            {"name": "lastAttestation", "type": "i64"},
            {"name": "bump", "type": "u8"},
        ]}},
    ],
}


def derive_vault_pda(program_id: Pubkey, owner: Pubkey) -> tuple[Pubkey, int]:
    return Pubkey.find_program_address([b"vault", bytes(owner)], program_id)


def derive_vault_token_pda(program_id: Pubkey, vault: Pubkey) -> tuple[Pubkey, int]:
    return Pubkey.find_program_address([b"vault_token", bytes(vault)], program_id)


class SolanaVaultClient:
    """Drives TradingVault: initialize → deposit → attest → withdraw."""

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
        from anchorpy import Idl as _Idl

        idl_obj = _Idl.from_json(json.dumps(_idl_with_address(IDL, str(self.program_id))))
        self._program = Program(idl_obj, self.program_id, self._provider)
        logger.info(f"Connected to TradingVault at {self.program_id} as {self.agent_address}")

    async def close(self):
        if self._client:
            await self._client.close()

    @property
    def program(self) -> Program:
        if self._program is None:
            raise RuntimeError("Not connected — call connect() first")
        return self._program

    async def read_vault(self, owner: Pubkey) -> Any:
        """Fetch and decode the vault PDA (raises if uninitialized)."""
        vault_pda, _ = derive_vault_pda(self.program_id, owner)
        return await self.program.account["Vault"].fetch(vault_pda)

    async def initialize(
        self,
        mint: Pubkey,
        min_deposit: int,
        max_tvl: int,
        attest_timelock: int,
        max_attestation_delta_bps: int,
    ) -> str:
        vault_pda, _ = derive_vault_pda(self.program_id, self.agent_address)
        tx = await self.program.rpc["initialize"](
            min_deposit,
            max_tvl,
            attest_timelock,
            max_attestation_delta_bps,
            ctx=_ctx({
                "vault": vault_pda,
                "owner": self.agent_address,
                "agent": self.agent_address,
                "mint": mint,
                "systemProgram": SYSTEM_PROGRAM_ID,
                "rent": RENT_SYSVAR_ID,
            }),
        )
        logger.info(f"Vault initialized: {tx}")
        return tx

    async def deposit(self, mint: Pubkey, user_token_account: Pubkey, amount: int) -> str:
        vault_pda, _ = derive_vault_pda(self.program_id, self.agent_address)
        vault_token_pda, _ = derive_vault_token_pda(self.program_id, vault_pda)
        tx = await self.program.rpc["deposit"](
            amount,
            ctx=_ctx({
                "vault": vault_pda,
                "user": self.agent_address,
                "userTokenAccount": user_token_account,
                "vaultTokenAccount": vault_token_pda,
                "mint": mint,
                "tokenProgram": TOKEN_PROGRAM_ID,
                "systemProgram": SYSTEM_PROGRAM_ID,
            }),
        )
        logger.info(f"Deposited {amount}: {tx}")
        return tx

    async def withdraw(self, mint: Pubkey, user_token_account: Pubkey, shares: int) -> str:
        vault_pda, _ = derive_vault_pda(self.program_id, self.agent_address)
        vault_token_pda, _ = derive_vault_token_pda(self.program_id, vault_pda)
        tx = await self.program.rpc["withdraw"](
            shares,
            ctx=_ctx({
                "vault": vault_pda,
                "user": self.agent_address,
                "userTokenAccount": user_token_account,
                "vaultTokenAccount": vault_token_pda,
                "mint": mint,
                "tokenProgram": TOKEN_PROGRAM_ID,
            }),
        )
        logger.info(f"Withdrew {shares} shares: {tx}")
        return tx

    async def attest_total_assets(self, new_total_assets: int) -> str:
        vault_pda, _ = derive_vault_pda(self.program_id, self.agent_address)
        tx = await self.program.rpc["attest_total_assets"](
            new_total_assets,
            ctx=_ctx({
                "vault": vault_pda,
                "agent": self.agent_address,
            }),
        )
        logger.info(f"Attested total_assets={new_total_assets}: {tx}")
        return tx
