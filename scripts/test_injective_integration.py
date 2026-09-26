"""
Stage 3: Connect Stage 1 + 2 into one script that:
1. Reads funding rate
2. If rate > threshold (e.g., +0.01%), calls placeAndLog for the short-perp leg
3. Prints packageId + tx hash
"""
import sys
import asyncio
import os
import json
from dotenv import load_dotenv

load_dotenv()

# Add proto directory to path
proto_dir = os.path.join(os.path.dirname(__file__), '..', 'proto')
sys.path.insert(0, proto_dir)

import grpc
from web3 import Web3
from eth_account import Account

from pyinjective.proto.exchange.injective_derivative_exchange_rpc_pb2 import FundingRatesRequest
from pyinjective.proto.exchange.injective_derivative_exchange_rpc_pb2_grpc import InjectiveDerivativeExchangeRPCStub

# Configuration
FUNDING_RATE_THRESHOLD_PCT = 0.01  # 0.01%
INJECTIVE_TESTNET_GRPC = "testnet.sentry.exchange.grpc.injective.network:443"
INJECTIVE_TESTNET_RPC = "https://k8s.testnet.json-rpc.injective.network/"
CHAIN_ID = 1439
EXCHANGE_PRECOMPILE = "0x0000000000000000000000000000000000000065"

# Market IDs on testnet
MARKETS = {
    "BTC/USDT PERP": "0x17ef48032cb24375ba7c2e39f384e56433bcab20cbee9a7357e4cba2eb00abe6",
}

PRIVATE_KEY = os.getenv("INJECTIVE_TESTNET_PRIVATE_KEY") or os.getenv("AGENT_WALLET_PRIVATE_KEY")
if PRIVATE_KEY and PRIVATE_KEY.startswith("0x"):
    PRIVATE_KEY = PRIVATE_KEY[2:]

if not PRIVATE_KEY:
    print("ERROR: Set INJECTIVE_TESTNET_PRIVATE_KEY environment variable")
    exit(1)

# Exchange precompile ABI
EXCHANGE_ABI = json.loads('''[
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
                    {"internalType": "uint256", "name": "triggerPrice", "type": "uint256"}
                ],
                "internalType": "struct IExchangeModule.DerivativeOrder",
                "name": "order",
                "type": "tuple"
            }
        ],
        "name": "createDerivativeLimitOrder",
        "outputs": [
            {
                "components": [
                    {"internalType": "string", "name": "orderHash", "type": "string"},
                    {"internalType": "string", "name": "cid", "type": "string"}
                ],
                "internalType": "struct IExchangeModule.CreateDerivativeLimitOrderResponse",
                "name": "response",
                "type": "tuple"
            }
        ],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]''')


def to_u256x18(value: float) -> int:
    return int(value * 1e18)


async def fetch_funding_rate(market_id: str) -> float:
    """Fetch latest funding rate for a market."""
    credentials = grpc.ssl_channel_credentials()
    channel = grpc.aio.secure_channel(INJECTIVE_TESTNET_GRPC, credentials)
    stub = InjectiveDerivativeExchangeRPCStub(channel)
    
    request = FundingRatesRequest(
        market_id=market_id,
        skip=0,
        limit=1,
        end_time=0
    )
    
    response = await stub.FundingRates(request)
    await channel.close()
    
    if response.funding_rates:
        return float(response.funding_rates[0].rate) * 100  # Convert to percentage
    return 0.0


def place_short_order(w3, account, exchange, market_id, subaccount_id):
    """Place a short perp order via EVM precompile."""
    # For short, we use "sell" order type
    quantity = to_u256x18(0.001)  # 0.001 BTC
    price = to_u256x18(50000)     # Way below market to avoid filling
    margin = to_u256x18(100)      # $100 margin
    
    order = {
        "marketID": market_id,
        "subaccountID": subaccount_id,
        "feeRecipient": account.address,
        "price": price,
        "quantity": quantity,
        "cid": f"funding-arb-{int(asyncio.get_event_loop().time())}",
        "orderType": "sell",  # Short = sell
        "margin": margin,
        "triggerPrice": 0
    }
    
    nonce = w3.eth.get_transaction_count(account.address)
    tx = exchange.functions.createDerivativeLimitOrder(account.address, order).build_transaction({
        "chainId": CHAIN_ID,
        "nonce": nonce,
        "gas": 500000,
        "gasPrice": w3.to_wei("1", "gwei"),
    })
    
    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    
    if receipt.status == 1:
        result = exchange.functions.createDerivativeLimitOrder(account.address, order).call()
        return tx_hash.hex(), result[0]
    else:
        raise Exception("Transaction failed")


async def main():
    print("=== TARS Funding Arbitrage - Stage 3 ===\n")
    
    # 1. Fetch funding rates
    print("1. Fetching funding rates from Injective testnet...")
    for name, market_id in MARKETS.items():
        rate_pct = await fetch_funding_rate(market_id)
        print(f"   {name}: {rate_pct:.6f}%")
        
        if rate_pct > FUNDING_RATE_THRESHOLD_PCT:
            print(f"   >>> Rate exceeds threshold ({FUNDING_RATE_THRESHOLD_PCT}%) - would place short order")
            
            # 2. Place order (if funded)
            w3 = Web3(Web3.HTTPProvider(INJECTIVE_TESTNET_RPC))
            account = Account.from_key(PRIVATE_KEY)
            subaccount_id = "0x" + account.address[2:].lower() + "00000000"
            exchange = w3.eth.contract(address=Web3.to_checksum_address(EXCHANGE_PRECOMPILE), abi=EXCHANGE_ABI)
            
            try:
                tx_hash, order_hash = place_short_order(w3, account, exchange, market_id, subaccount_id)
                print(f"   >>> Order placed!")
                print(f"       Tx Hash: {tx_hash}")
                print(f"       Order Hash: {order_hash}")
            except Exception as e:
                print(f"   >>> Failed to place order: {e}")
                print(f"       (Account may need testnet INJ from faucet)")
        else:
            print(f"   >>> Rate below threshold - no action")


if __name__ == "__main__":
    asyncio.run(main())