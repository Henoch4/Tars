"""
Deploy repo contracts to X Layer Testnet using web3.py.

Contracts:
  - TradeAuditTrail (audit log; no constructor args)
  - TradingVault (pooled USDT vault; needs VAULT_* env vars below)
  - AgentRegistry (capability/reputation surface; needs AUDIT_CONTRACT_ADDRESS)

Prerequisites:
  pip install web3 eth-account eth-abi
  Set environment:
    XLAYER_RPC_URL=https://testnet-rpc.xlayer.tech
    DEPLOYER_PRIVATE_KEY=0x...   # fresh testnet-only key, NEVER a reused mainnet key
  TradingVault also needs:
    VAULT_ASSET_ADDRESS=<USDT address on X Layer>
    VAULT_AGENT_ADDRESS=<agent EOA that will trade/attest>
    VAULT_MIN_DEPOSIT=<asset minimal units, 6 dp for USDT>
    VAULT_MAX_TVL=<asset minimal units>
    VAULT_ATTEST_TIMELOCK=<seconds between NAV attestations>
    VAULT_MAX_ATTESTATION_DELTA_BPS=<max NAV change per attest, bps, default 1000>
  AgentRegistry also needs:
    AUDIT_CONTRACT_ADDRESS=<deployed TradeAuditTrail>

Usage:
  python deploy.py                   # deploy TradeAuditTrail (default)
  python deploy.py TradingVault      # deploy TradingVault
  python deploy.py AgentRegistry     # deploy AgentRegistry
"""
import json
import os
import sys
from pathlib import Path

from web3 import Web3 as Web
from eth_account import Account

CONTRACT_DIR = Path(__file__).resolve().parent.parent  # contracts/
RPC_URL = os.getenv("XLAYER_RPC_URL", "https://testrpc.xlayer.tech/terigon")
PRIVATE_KEY = os.getenv("DEPLOYER_PRIVATE_KEY")
# X Layer Testnet, per OKX docs + chainlist (verified 2026-10; the "195"
# value is the retired testnet). Pinned AND verified at runtime: _chain_id
# aborts unless the RPC reports exactly this (test_chain_id_consistency
# pins the literal; the abort pins reality).
CHAIN_ID = 1952


def _artifacts(name):
    abi_path = CONTRACT_DIR / "artifacts" / f"{name}_abi.json"
    bin_path = CONTRACT_DIR / "artifacts" / f"{name}_bytecode.txt"
    if not abi_path.exists() or not bin_path.exists():
        print(f"ERROR: Compiled artifacts not found for {name}.")
        print("Run the Solidity compiler first:")
        print('  python compile_contract.py')
        sys.exit(1)
    return json.loads(abi_path.read_text()), bin_path.read_text().strip()


def _prepare():
    if not PRIVATE_KEY:
        print("ERROR: Set DEPLOYER_PRIVATE_KEY environment variable")
        print("Example: set DEPLOYER_PRIVATE_KEY=0x...")
        sys.exit(1)

    print(f"Connecting to X Layer Testnet: {RPC_URL}")
    w3 = Web(Web.HTTPProvider(RPC_URL))

    if not w3.is_connected():
        print("ERROR: Cannot connect to X Layer RPC")
        sys.exit(1)

    print(f"Connected. Chain ID: {w3.eth.chain_id}")
    print(f"Latest block: {w3.eth.block_number}")

    account = Account.from_key(PRIVATE_KEY)
    print(f"Deploying from: {account.address}")
    balance = w3.eth.get_balance(account.address)
    balance_native = w3.from_wei(balance, "ether")
    print(f"Balance: {balance_native} OKB (native gas token on X Layer)")

    if balance < w3.to_wei("0.01", "ether"):
        print("WARNING: Low balance. You need testnet OKB from the faucet.")
        print("Get OKB (gas token for X Layer) at: https://faucet.xlayer.tech")
        print("Continuing anyway...")

    return w3, account


def _chain_id(w3):
    """Chain ID verified against the RPC we're actually talking to. The
    pinned CHAIN_ID is the expectation; the endpoint is the authority —
    on mismatch we abort instead of signing for an unexpected chain."""
    cid = w3.eth.chain_id
    print(f"Chain ID (from RPC): {cid}")
    if cid != CHAIN_ID:
        print(f"ERROR: RPC reports chain {cid}, expected {CHAIN_ID} "
              f"(X Layer Testnet). Refusing to sign.")
        sys.exit(1)
    return cid


def _send(w3, account, contract, constructor_args, label):
    print(f"\nDeploying {label} contract...")
    Contract = w3.eth.contract(abi=contract[0], bytecode=contract[1])

    nonce = w3.eth.get_transaction_count(account.address)
    chain_id = _chain_id(w3)
    tx = Contract.constructor(*constructor_args).build_transaction({
        "chainId": chain_id,
        "gas": 8000000,
        "gasPrice": w3.to_wei("1", "gwei"),
        "nonce": nonce,
    })

    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    print("Signed transaction. Sending...")

    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Transaction hash: {tx_hash.hex()}")
    print("Waiting for confirmation...")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    contract_address = receipt.get('contractAddress', receipt.get('contract_address'))

    print(f"\nContract deployed!")
    print(f"Address: {contract_address}")
    print(f"Block: {receipt.get('blockNumber', receipt.get('block_number', 'unknown'))}")
    print(f"Gas used: {receipt.get('gasUsed', receipt.get('gas_used', 'unknown'))}")
    print(f"Status: {'SUCCESS' if receipt.get('status', 0) == 1 else 'FAILED'}")

    deploy_info = {
        "contract": label,
        "address": contract_address,
        "chain_id": chain_id,
        "tx_hash": tx_hash.hex(),
        "gas_used": receipt.get('gasUsed', receipt.get('gas_used', 0)),
        "block_number": receipt.get('blockNumber', receipt.get('block_number', 0)),
        "deployer": account.address,
    }
    info_path = CONTRACT_DIR / "deployment.json"
    info_path.write_text(json.dumps(deploy_info, indent=2))
    print(f"\nDeployment info saved to {info_path}")
    return contract_address


def deploy_audit_trail():
    w3, account = _prepare()
    contract = _artifacts("TradeAuditTrail")
    address = _send(w3, account, contract, (), "TradeAuditTrail")

    deployed = w3.eth.contract(address=address, abi=contract[0])
    count = deployed.functions.getDecisionCount().call()
    print(f"\nVerification: getDecisionCount() = {count}")

    print(f"\nNext steps:")
    print(f"  1. Set AUDIT_CONTRACT_ADDRESS={address}")
    print(f"  2. Run setRiskParams() to configure the agent")
    print(f"  3. Start the trading agent: uvicorn src.main:app --port 8000")
    return address


def deploy_trading_vault():
    w3, account = _prepare()
    contract = _artifacts("TradingVault")

    asset = os.getenv("VAULT_ASSET_ADDRESS")
    agent_addr = os.getenv("VAULT_AGENT_ADDRESS")
    min_deposit = os.getenv("VAULT_MIN_DEPOSIT")
    max_tvl = os.getenv("VAULT_MAX_TVL")
    attest_timelock = os.getenv("VAULT_ATTEST_TIMELOCK", "3600")
    max_attestation_delta_bps = os.getenv("VAULT_MAX_ATTESTATION_DELTA_BPS", "1000")

    missing = [k for k, v in {"VAULT_ASSET_ADDRESS": asset,
                              "VAULT_AGENT_ADDRESS": agent_addr,
                              "VAULT_MIN_DEPOSIT": min_deposit,
                              "VAULT_MAX_TVL": max_tvl}.items() if not v]
    if missing:
        print(f"ERROR: Missing env vars: {', '.join(missing)}")
        print("TradingVault needs USDT asset address, agent EOA, and caps.")
        sys.exit(1)

    address = _send(w3, account, contract,
                    (asset, agent_addr, int(min_deposit), int(max_tvl),
                     int(attest_timelock), int(max_attestation_delta_bps)),
                    "TradingVault")

    deployed = w3.eth.contract(address=address, abi=contract[0])
    print(f"\nVerification: totalAssets() = {deployed.functions.totalAssets().call()}")
    print(f"  asset = {asset}")
    print(f"  agent = {agent_addr}")
    print(f"  MIN_DEPOSIT = {min_deposit}, MAX_TVL = {max_tvl}, ATTEST_TIMELOCK = {attest_timelock}")
    print(f"  MAX_ATTESTATION_DELTA_BPS = {max_attestation_delta_bps}")
    return address


def deploy_agent_registry():
    w3, account = _prepare()
    contract = _artifacts("AgentRegistry")

    audit_addr = os.getenv("AUDIT_CONTRACT_ADDRESS", "").strip()
    if not audit_addr:
        print("ERROR: Missing env var: AUDIT_CONTRACT_ADDRESS")
        print("AgentRegistry reads reputation from the deployed TradeAuditTrail.")
        sys.exit(1)

    address = _send(w3, account, contract, (audit_addr,), "AgentRegistry")

    deployed = w3.eth.contract(address=address, abi=contract[0])
    print(f"\nVerification: auditTrail() = "
          f"{deployed.functions.auditTrail().call()}")
    print(f"\nNext steps:")
    print(f"  1. Set REGISTRY_CONTRACT_ADDRESS={address}")
    print(f"  2. Register the agent: register(endpoint, capsHash, tiersVersion)")
    return address


if __name__ == "__main__":
    choice = sys.argv[1] if len(sys.argv) > 1 else "TradeAuditTrail"
    if choice == "TradingVault":
        deploy_trading_vault()
    elif choice == "AgentRegistry":
        deploy_agent_registry()
    else:
        deploy_audit_trail()
