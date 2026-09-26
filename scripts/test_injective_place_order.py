"""
Stage 2: Place a test order on Injective testnet perp book via EVM precompile (0x65).
Uses web3.py to call the exchange precompile.
"""
import os
import json
from web3 import Web3
from eth_account import Account

# Testnet configuration
RPC_URL = "https://k8s.testnet.json-rpc.injective.network/"
CHAIN_ID = 1439
EXCHANGE_PRECOMPILE = "0x0000000000000000000000000000000000000065"

# Load private key from env (create a throwaway testnet key)
# Also check .env file
from dotenv import load_dotenv
load_dotenv()

PRIVATE_KEY = os.getenv("INJECTIVE_TESTNET_PRIVATE_KEY") or os.getenv("AGENT_WALLET_PRIVATE_KEY")
if not PRIVATE_KEY:
    print("ERROR: Set INJECTIVE_TESTNET_PRIVATE_KEY environment variable")
    print("Get testnet INJ from: https://testnet.faucet.injective.network/")
    exit(1)

# Strip 0x prefix if present
if PRIVATE_KEY.startswith("0x"):
    PRIVATE_KEY = PRIVATE_KEY[2:]

# Exchange precompile ABI for createDerivativeLimitOrder
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
    """Convert human-readable value to UFixed256x18 (1e18 scale)"""
    return int(value * 1e18)


def main():
    # Connect to testnet
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print(f"ERROR: Cannot connect to {RPC_URL}")
        exit(1)
    
    print(f"Connected to Injective testnet (chain_id={w3.eth.chain_id})")
    
    # Setup account
    account = Account.from_key(PRIVATE_KEY)
    print(f"Using account: {account.address}")
    
    # Check balance
    balance = w3.eth.get_balance(account.address)
    balance_inj = w3.from_wei(balance, "ether")
    print(f"Balance: {balance_inj} INJ")
    
    if balance < w3.to_wei("0.01", "ether"):
        print("WARNING: Low balance. Get testnet INJ from https://testnet.faucet.injective.network/")
    
    # Exchange precompile contract
    exchange = w3.eth.contract(address=Web3.to_checksum_address(EXCHANGE_PRECOMPILE), abi=EXCHANGE_ABI)
    
    # Test order parameters
    # Market ID from Stage 1: BTC/USDT PERP
    market_id = "0x17ef48032cb24375ba7c2e39f384e56433bcab20cbee9a7357e4cba2eb00abe6"
    
    # Subaccount ID: derived from account address + nonce 0
    # Format: 0x + address (20 bytes) + nonce (4 bytes)
    subaccount_id = "0x" + account.address[2:].lower() + "00000000"
    
    # Small test order: 0.001 BTC at $50,000 (way below market to avoid filling)
    # Using limit buy order ("buy" = buy)
    quantity = to_u256x18(0.001)  # 0.001 BTC in 1e18 scale
    price = to_u256x18(50000)     # 50,000 USDT in 1e18 scale
    margin = to_u256x18(100)      # $100 margin in 1e18 scale
    leverage = 1  # 1x
    
    print(f"\nPlacing test limit order:")
    print(f"  Market ID: {market_id}")
    print(f"  Subaccount: {subaccount_id}")
    print(f"  Quantity: 0.001 BTC ({quantity})")
    print(f"  Price: $50,000 ({price})")
    print(f"  Margin: $100 ({margin})")
    print(f"  Order Type: buy")
    
    # Build transaction
    nonce = w3.eth.get_transaction_count(account.address)
    
    order = {
        "marketID": market_id,
        "subaccountID": subaccount_id,
        "feeRecipient": account.address,
        "price": price,
        "quantity": quantity,
        "cid": "test-order-1",
        "orderType": "buy",  # "buy", "sell", "buyPostOnly", "sellPostOnly"
        "margin": margin,
        "triggerPrice": 0
    }
    
    tx = exchange.functions.createDerivativeLimitOrder(
        account.address,
        order
    ).build_transaction({
        "chainId": CHAIN_ID,
        "nonce": nonce,
        "gas": 500000,
        "gasPrice": w3.to_wei("1", "gwei"),
    })
    
    print(f"\nTransaction built. Signing...")
    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    print(f"Signed. Sending...")
    
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Transaction sent! Hash: {tx_hash.hex()}")
    print("Waiting for confirmation...")
    
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    
    print(f"\nTransaction confirmed!")
    print(f"Block: {receipt.blockNumber}")
    print(f"Gas used: {receipt.gasUsed}")
    print(f"Status: {'SUCCESS' if receipt.status == 1 else 'FAILED'}")
    
    if receipt.status != 1:
        print("Transaction failed. Check revert reason.")
    
    # Decode the return value
    try:
        result = exchange.functions.createDerivativeLimitOrder(account.address, order).call()
        print(f"Order Hash: {result[0]}")
        print(f"CID: {result[1]}")
    except Exception as e:
        print(f"Could not decode return value: {e}")
    
    return tx_hash.hex()


if __name__ == "__main__":
    main()