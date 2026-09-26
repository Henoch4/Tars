"""Injective exchange client (testnet-first, EVM-precompile execution).

Promoted from ``scripts/test_injective_*.py`` (Stages 1-3, proven live on
2026-09-26 against testnet gRPC + JSON-RPC). Same ``run()`` surface the
agent uses on OKX/Binance:

    client = await InjectiveClient(...)
    await client.run("market", "trades", "BTC-USDT-SWAP", "--limit", "50")
    await client.run("market", "funding-rate", "BTC-USDT-SWAP")

Return shapes match the OKX CLI so ``agent._fetch_market_data`` parses them
unchanged: trades are ``{"px","sz","ts"}`` dicts, funding is
``[{"fundingRate": str}]``.

Design notes:
- Market data over Injective Exchange gRPC (indexer); orders over the EVM
  exchange precompile (0x65) via web3.py — the same keypair works on both.
- ``InjectiveClientError`` subclasses ``OkxCliError`` so every existing
  ``except OkxCliError`` in agent/executor keeps working.
- Heavy deps (grpc, web3) import lazily inside methods: offline pytest and
  ``EXCHANGE=okx`` runs never pay for them.
- Audit routing: Injective orders settle through the EVM precompile, so
  ``chain_router`` default (evm) already covers INJ perps. No router change.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

from .okx_cli import OkxCliError

logger = logging.getLogger(__name__)


class InjectiveClientError(OkxCliError):
    """Injective failure, catch-compatible with existing OkxCliError handlers.

    Takes a plain message; fills the OkxCliError (args/returncode/stderr)
    shape so handlers reading ``.stderr``/``.kind`` keep working.
    """

    def __init__(self, message: str, kind: str = OkxCliError.TRANSPORT):
        super().__init__(["injective"], 1, message, kind)


# Proven live 2026-09-26 (testnet.sentry.exchange.grpc.injective.network).
INJECTIVE_TESTNET_GRPC = "testnet.sentry.exchange.grpc.injective.network:443"
INJECTIVE_TESTNET_RPC = "https://k8s.testnet.json-rpc.injective.network/"
INJECTIVE_TESTNET_CHAIN_ID = 1439
EXCHANGE_PRECOMPILE = "0x0000000000000000000000000000000000000065"

# Testnet market ids (add more as the book is mapped).
DEFAULT_TESTNET_MARKETS: dict[str, str] = {
    "BTC-USDT-SWAP": "0x17ef48032cb24375ba7c2e39f384e56433bcab20cbee9a7357e4cba2eb00abe6",
}

_EXCHANGE_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "sender", "type": "address"},
            {
                "components": [
                    {"internalType": "string", "name": "marketID", "type": "string"},
                    {"internalType": "string", "name": "subaccountID", "type": "string"},
                    {"internalType": "string", "name": "feeRecipient", "type": "string"},
                    {"internalType": "uint256", "name": "price", "type": "uint256"},
                    {"internalType": "uint256", "name": "quantity", "type": "uint256"},
                    {"internalType": "string", "name": "cid", "type": "string"},
                    {"internalType": "string", "name": "orderType", "type": "string"},
                    {"internalType": "uint256", "name": "margin", "type": "uint256"},
                    {"internalType": "uint256", "name": "triggerPrice", "type": "uint256"},
                ],
                "internalType": "struct IExchangeModule.DerivativeOrder",
                "name": "order",
                "type": "tuple",
            },
        ],
        "name": "createDerivativeLimitOrder",
        "outputs": [
            {
                "components": [
                    {"internalType": "string", "name": "orderHash", "type": "string"},
                    {"internalType": "string", "name": "cid", "type": "string"},
                ],
                "internalType": "struct IExchangeModule.CreateDerivativeLimitOrderResponse",
                "name": "response",
                "type": "tuple",
            }
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


@dataclass
class InjectiveConfig:
    grpc_endpoint: str = INJECTIVE_TESTNET_GRPC
    rpc_url: str = INJECTIVE_TESTNET_RPC
    chain_id: int = INJECTIVE_TESTNET_CHAIN_ID
    private_key: str = ""
    testnet: bool = True
    markets: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TESTNET_MARKETS))


def _to_u256x18(value: float) -> int:
    """Human units -> precompile UFixed256x18 (proven in Stage-2 script)."""
    return int(value * 1e18)


def _ensure_proto_path() -> None:
    """Put the vendored ``proto/`` dir on sys.path (no CWD dependence)."""
    import pathlib
    import sys

    proto_dir = str(pathlib.Path(__file__).resolve().parent.parent / "proto")
    if proto_dir not in sys.path:
        sys.path.insert(0, proto_dir)


class InjectiveClient:
    """Async Injective client speaking the agent's ``run()`` dialect."""

    def __init__(self, config: InjectiveConfig):
        self.config = config

    # --- internal plumbing (lazy heavy imports) ---

    def _stub(self, grpc_mod):
        _ensure_proto_path()
        from pyinjective.proto.exchange import (
            injective_derivative_exchange_rpc_pb2_grpc as pb_grpc,
        )

        channel = grpc_mod.aio.secure_channel(
            self.config.grpc_endpoint, grpc_mod.ssl_channel_credentials()
        )
        return pb_grpc.InjectiveDerivativeExchangeRPCStub(channel), channel

    def _messages(self):
        _ensure_proto_path()
        from pyinjective.proto.exchange import (
            injective_derivative_exchange_rpc_pb2 as pb,
        )

        return pb

    def _w3(self):
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(self.config.rpc_url))
        if not w3.is_connected():
            raise InjectiveClientError(f"Cannot connect to {self.config.rpc_url}")
        return w3

    def _account(self):
        from eth_account import Account

        key = (self.config.private_key or "").strip()
        if not key:
            raise InjectiveClientError("INJECTIVE_TESTNET_PRIVATE_KEY is not set")
        return Account.from_key(key)

    def resolve_market(self, asset: str) -> str:
        """Map an agent symbol (or pass through a raw 0x market id)."""
        asset = (asset or "").strip()
        if asset.startswith("0x") and len(asset) == 66:
            return asset
        try:
            return self.config.markets[asset]
        except KeyError:
            known = sorted(self.config.markets)
            raise InjectiveClientError(
                f"Unknown Injective market for {asset!r} (known: {known})"
            ) from None

    # --- market data (gRPC, proven Stage 1) ---

    async def get_funding_rate(self, asset: str, timeout: float = 10.0) -> dict:
        """Latest funding rate as ``{"fundingRate": str}`` (fraction, OKX shape)."""
        try:
            import grpc
        except ImportError as e:
            raise InjectiveClientError(f"grpcio is required for Injective: {e}") from e
        pb = self._messages()
        market_id = self.resolve_market(asset)
        stub, channel = self._stub(grpc)
        try:
            resp = await stub.FundingRates(
                pb.FundingRatesRequest(market_id=market_id, limit=1),
                timeout=timeout,
            )
        except Exception as e:
            raise InjectiveClientError(f"FundingRates failed for {asset}: {e}") from e
        finally:
            await channel.close()
        if resp.funding_rates:
            return {"fundingRate": str(resp.funding_rates[0].rate)}
        return {"fundingRate": "0"}

    async def get_trades(self, asset: str, limit: int = 50, timeout: float = 10.0) -> list[dict]:
        """Recent trades as ``[{"px","sz","ts"}]`` (agent shape).

        Indexer returns human decimal strings (verified live); ``ts`` is ms.
        """
        try:
            import grpc
        except ImportError as e:
            raise InjectiveClientError(f"grpcio is required for Injective: {e}") from e
        pb = self._messages()
        market_id = self.resolve_market(asset)
        stub, channel = self._stub(grpc)
        try:
            resp = await stub.Trades(
                pb.TradesRequest(market_id=market_id, limit=max(1, min(limit, 100))),
                timeout=timeout,
            )
        except Exception as e:
            raise InjectiveClientError(f"Trades failed for {asset}: {e}") from e
        finally:
            await channel.close()
        out = []
        for t in resp.trades:
            try:
                out.append(
                    {
                        "px": str(t.position_delta.execution_price),
                        "sz": str(t.position_delta.execution_quantity),
                        "ts": str(t.executed_at),
                    }
                )
            except Exception:
                continue
        return out

    # --- execution (EVM precompile 0x65, proven Stage 2) ---

    def subaccount_id(self, address: str) -> str:
        return "0x" + address[2:].lower() + "00000000"

    def place_derivative_limit_order(
        self,
        market_id: str,
        side: str,
        price: float,
        quantity: float,
        margin: float,
        cid: str = "",
    ) -> dict:
        """Blocking precompile order. Prices/qty/margin in human units."""
        from web3 import Web3

        side = side.lower()
        if side not in ("buy", "sell"):
            raise InjectiveClientError(f"side must be buy|sell, got {side!r}")
        w3 = self._w3()
        account = self._account()
        import time

        exchange = w3.eth.contract(
            address=Web3.to_checksum_address(EXCHANGE_PRECOMPILE), abi=_EXCHANGE_ABI
        )
        order = {
            "marketID": market_id,
            "subaccountID": self.subaccount_id(account.address),
            "feeRecipient": account.address,
            "price": _to_u256x18(price),
            "quantity": _to_u256x18(quantity),
            "cid": cid or f"tars-{int(time.time())}",
            "orderType": side,
            "margin": _to_u256x18(margin),
            "triggerPrice": 0,
        }
        tx = exchange.functions.createDerivativeLimitOrder(
            account.address, order
        ).build_transaction(
            {
                "chainId": self.config.chain_id,
                "nonce": w3.eth.get_transaction_count(account.address),
                "gas": 500000,
                "gasPrice": w3.to_wei("1", "gwei"),
            }
        )
        signed = w3.eth.account.sign_transaction(tx, self.config.private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt.status != 1:
            raise InjectiveClientError(f"order tx reverted: {tx_hash.hex()}")
        try:
            result = exchange.functions.createDerivativeLimitOrder(
                account.address, order
            ).call()
            order_hash = result[0]
        except Exception:
            order_hash = ""
        return {
            "tx_hash": tx_hash.hex(),
            "order_hash": order_hash,
            "block": receipt.blockNumber,
            "status": "success",
        }

    # --- agent surface ---

    async def check_auth(self) -> dict:
        """EVM balance probe (no key = explicit error, never silent)."""
        try:
            w3 = await asyncio.to_thread(self._w3)
            account = self._account()
            balance = await asyncio.to_thread(w3.eth.get_balance, account.address)
            return {
                "ok": True,
                "address": account.address,
                "balance_inj": str(w3.from_wei(balance, "ether")),
                "testnet": self.config.testnet,
            }
        except InjectiveClientError:
            raise
        except Exception as e:
            raise InjectiveClientError(f"check_auth failed: {e}") from e

    async def balance_all(self) -> dict:
        auth = await self.check_auth()
        return {"totalEq": auth["balance_inj"], "ccy": "INJ", **auth}

    async def run(self, *args: str, use_global_flags: bool = True) -> object:
        """CLI-dialect dispatcher: ``market trades|funding-rate``.

        Shapes match the OKX CLI so agent parsing is unchanged.
        """
        parts = [str(a) for a in args if not str(a).startswith("--")]
        flags = [str(a) for a in args if str(a).startswith("--")]
        if len(parts) >= 3 and parts[0] == "market" and parts[1] == "trades":
            limit = 50
            for i, f in enumerate(flags):
                if f == "--limit" and i + 1 < len(flags):
                    try:
                        limit = int(flags[i + 1])
                    except ValueError:
                        pass
            return await self.get_trades(parts[2], limit=limit)
        if len(parts) >= 3 and parts[0] == "market" and parts[1] == "funding-rate":
            return [await self.get_funding_rate(parts[2])]
        raise InjectiveClientError(
            f"Unsupported Injective run() args: {list(args)} "
            "(supported: market trades, market funding-rate)"
        )

    async def close(self) -> None:
        return None


def config_from_env() -> InjectiveConfig:
    """Build config from env. Key presence only — never log values."""
    return InjectiveConfig(
        grpc_endpoint=os.getenv("INJECTIVE_GRPC", INJECTIVE_TESTNET_GRPC),
        rpc_url=os.getenv("INJECTIVE_RPC_URL", INJECTIVE_TESTNET_RPC),
        chain_id=int(os.getenv("INJECTIVE_CHAIN_ID", str(INJECTIVE_TESTNET_CHAIN_ID))),
        private_key=os.getenv("INJECTIVE_TESTNET_PRIVATE_KEY", ""),
        testnet=os.getenv("INJECTIVE_TESTNET", "true").lower() == "true",
    )
