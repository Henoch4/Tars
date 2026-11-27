"""X Layer Testnet contract state verification — read-only, no keys, no writes.

Connects to X Layer Testnet (chainId 1952) via public RPC and reads the
deployed TradeAuditTrail.sol state:
  - chain_id matches expected 1952
  - contract is deployed at AUDIT_CONTRACT_ADDRESS (code size > 0)
  - getAgentDailyStats(agent) → volume / loss / trades
  - getDecisionCount() / getExecutionCount()
  - getRecentDecisions(N) → last N decisions (asset, signal, confidence, sizeUsd, executed)
  - killSwitchActive(agent) / killSwitchReason(agent)
  - agentRiskParams(agent) → current on-chain risk params
  - eth_estimateGas for logDecision() and recordExecution() (dummy, read-only)

No private keys used. No state-changing calls. Safe to run during G1 window.

Usage:
    python scripts/xlayer_contract_verify.py [rpc_url] [contract_address] [agent_address]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from web3 import Web3

DEFAULT_RPC = "https://testnet-rpc.xlayer.tech"
DEFAULT_CHAIN_ID = 1952
# From vercel.json / scripts/test_onchain.py — TradeAuditTrail on testnet
DEFAULT_CONTRACT = "0x6019b96e9d0Ba17588eb22579d9c2dEf0473d07c"
# Agent address — placeholder; operator sets this
DEFAULT_AGENT = "0x0000000000000000000000000000000000000000"


def load_abi() -> list:
    abi_path = Path("contracts/artifacts/TradeAuditTrail_abi.json")
    if not abi_path.exists():
        raise FileNotFoundError(f"ABI not found: {abi_path}")
    return json.loads(abi_path.read_text(encoding="utf-8"))


def bytes32_short(b: bytes | str) -> str:
    if isinstance(b, str):
        return b[:10] + "..."
    return b.hex()[:10] + "..."


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    rpc_url = argv[0] if len(argv) > 0 else DEFAULT_RPC
    contract_addr = argv[1] if len(argv) > 1 else DEFAULT_CONTRACT
    agent_addr = argv[2] if len(argv) > 2 else DEFAULT_AGENT

    print("=== X Layer Testnet contract state verification (read-only) ===")
    print(f"RPC: {rpc_url}")
    print(f"Contract: {contract_addr}")
    print(f"Agent: {agent_addr}")
    print()

    # -- connect -----------------------------------------------------------
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    try:
        connected = w3.is_connected()
    except Exception as e:
        print(f"CONNECT: FAIL — {type(e).__name__}: {e}")
        return 1

    if not connected:
        print("CONNECT: FAIL — cannot reach RPC")
        return 1

    chain_id = w3.eth.chain_id
    print(f"CONNECT: OK — chain_id={chain_id} (expected {DEFAULT_CHAIN_ID})")
    if chain_id != DEFAULT_CHAIN_ID:
        print("  WARNING: chain_id mismatch — verify RPC URL")
    print()

    # -- contract code check ----------------------------------------------
    code = w3.eth.get_code(Web3.to_checksum_address(contract_addr))
    print(f"CONTRACT: code_size={len(code)} bytes")
    if len(code) == 0:
        print("  FAIL: no contract code at this address — not deployed or wrong address")
        return 1
    print("  OK: contract code present")
    print()

    # -- bind contract -----------------------------------------------------
    abi = load_abi()
    contract = w3.eth.contract(address=Web3.to_checksum_address(contract_addr), abi=abi)

    # -- read-only state calls ---------------------------------------------
    print("--- read-only state ---")

    dec_count = exec_count = None
    halted = reason = None
    params = None
    stats = None
    last_id = None
    recent = None

    # getDecisionCount / getExecutionCount
    try:
        dec_count = contract.functions.getDecisionCount().call()
        exec_count = contract.functions.getExecutionCount().call()
        print(f"  decision_count: {dec_count}")
        print(f"  execution_count: {exec_count}")
    except Exception as e:
        print(f"  FAIL reading counts: {type(e).__name__}: {e}")

    # kill switch status for agent
    try:
        halted = contract.functions.killSwitchActive(Web3.to_checksum_address(agent_addr)).call()
        reason = contract.functions.killSwitchReason(Web3.to_checksum_address(agent_addr)).call()
        print(f"  kill_switch_active: {halted}")
        print(f"  kill_switch_reason: {reason!r}")
    except Exception as e:
        print(f"  FAIL reading kill switch: {type(e).__name__}: {e}")

    # agent risk params
    try:
        params = contract.functions.agentRiskParams(Web3.to_checksum_address(agent_addr)).call()
        print(f"  agent_risk_params:")
        print(f"    max_position_size_usd: {params[0]}")
        print(f"    max_daily_loss_usd: {params[1]}")
        print(f"    max_leverage_bps: {params[2]}")
        print(f"    min_confidence_bps: {params[3]}")
        print(f"    is_market_order: {params[4]}")
    except Exception as e:
        print(f"  FAIL reading risk params: {type(e).__name__}: {e}")

    # daily stats
    try:
        stats = contract.functions.getAgentDailyStats(Web3.to_checksum_address(agent_addr)).call()
        print(f"  daily_stats(agent):")
        print(f"    volume: {stats[0]}")
        print(f"    loss: {stats[1]}")
        print(f"    trades: {stats[2]}")
    except Exception as e:
        print(f"  FAIL reading daily stats: {type(e).__name__}: {e}")

    # last decision id
    try:
        last_id = contract.functions.lastDecisionId(Web3.to_checksum_address(agent_addr)).call()
        print(f"  last_decision_id: {bytes32_short(last_id)}")
    except Exception as e:
        print(f"  FAIL reading last decision id: {type(e).__name__}: {e}")

    print()

    # -- recent decisions ---------------------------------------------------
    print("--- recent decisions (last 5) ---")
    try:
        recent = contract.functions.getRecentDecisions(5).call()
        if not recent:
            print("  (no decisions logged yet)")
        for d in recent:
            print(f"  decision_id={bytes32_short(d[0])} "
                  f"package_id={bytes32_short(d[1])} "
                  f"agent={d[2]} "
                  f"asset={d[3]} signal={d[4]} strategy={d[5]} "
                  f"confidence={d[6]}bps "
                  f"entry_price={d[7]} size_usd={d[8]} "
                  f"timestamp={d[9]} "
                  f"executed={d[12]} is_short={d[13]}")
    except Exception as e:
        print(f"  FAIL reading recent decisions: {type(e).__name__}: {e}")

    print()

    # -- gas estimates (read-only, eth_estimateGas does not execute) -------
    print("--- gas estimates (eth_estimateGas, read-only) ---")

    # Build a dummy DecisionInput for logDecision estimate.
    # web3.py requires structs passed as dicts keyed by Solidity member name.
    dummy_input = {
        "decisionId": Web3.to_bytes(hexstr="0x" + "11" * 32),
        "packageId": Web3.to_bytes(hexstr="0x" + "22" * 32),
        "asset": "TEST",
        "signal": "LONG",
        "strategy": "momentum",
        "confidence": 7000,
        "entryPrice": 100000000,   # 1.0 in 1e8 fixed point (per audit_logger.py)
        "sizeUsd": 2500000000,     # $25.00 in 1e8 fixed point
        "riskHash": Web3.to_bytes(hexstr="0x" + "33" * 32),
        "signature": b"",
        "isShort": False,
    }
    try:
        gas_log = contract.functions.logDecision(dummy_input).estimate_gas()
        print(f"  logDecision: ~{gas_log:,} gas")
    except Exception as e:
        print(f"  logDecision estimate FAIL: {type(e).__name__}: {e}")

    try:
        gas_exec = contract.functions.recordExecution(
            Web3.to_bytes(hexstr="0x" + "44" * 32),  # decisionId
            1000000000000000000,                     # fillPrice
            1000000000000000000,                     # fillSizeUsd
            0,                                       # feeUsd
            True,                                    # success
        ).estimate_gas()
        print(f"  recordExecution: ~{gas_exec:,} gas")
    except Exception as e:
        print(f"  recordExecution estimate FAIL: {type(e).__name__}: {e}")

    print()
    print("=== verification complete ===")
    print()
    print("--- findings summary ---")
    print(f"  RPC reachable: YES (chain_id={chain_id})")
    print(f"  Contract deployed: YES (code_size={len(code)} bytes)")
    print(f"  Contract address: {contract_addr}")
    print(f"  Matches vercel.json AUDIT_CONTRACT_ADDRESS: {contract_addr.lower() == DEFAULT_CONTRACT.lower()}")
    print(f"  Decisions logged: {dec_count if 'dec_count' in dir() else 0}")
    print(f"  Executions recorded: {exec_count if 'exec_count' in dir() else 0}")
    print(f"  Kill switch active: {halted if 'halted' in dir() else 'unknown'}")
    print(f"  Risk params set: {'NO (all zeros — setRiskParams not yet called)' if params and all(p == 0 for p in params) else 'YES'}")
    print()
    print("  Gas estimates: not obtained (read-only). logDecision requires a valid")
    print("  agent signature; recordExecution requires an existing decision. Both")
    print("  need AGENT_WALLET_PRIVATE_KEY — intentionally out of scope here.")
    print()
    print("=== end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
