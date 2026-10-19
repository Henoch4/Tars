"""TODO 2.9: per-asset on-chain circuit breaker (eth-tester EVM).

TradeAuditTrail.sol enforced only GLOBAL per-agent limits (position size,
daily loss/trades, kill switch). A runaway single asset could consume the
entire daily budget while every global check still passed. The per-asset
daily volume cap is defense-in-depth under the off-chain counters: even if
the off-chain gate is bypassed, the chain rejects the excess decision.

Compiling the real contract here (py-solc-x via-ir) doubles as the
recompile verification for the Solidity change.
"""
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak
from solcx import compile_source
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

SOLC_VERSION = "0.8.30"
BTC = "BTC-USDT-SWAP"
ETH = "ETH-USDT-SWAP"


@pytest.fixture(scope="session")
def compiled():
    path = Path(__file__).resolve().parent.parent / "contracts" / "contracts" / "TradeAuditTrail.sol"
    src = path.read_text(encoding="utf-8")
    result = compile_source(src, solc_version=SOLC_VERSION,
                            output_values=["abi", "bin"], via_ir=True)
    data = result.get("<stdin>:TradeAuditTrail")
    if data is None:
        raise KeyError("TradeAuditTrail not found in compile output")
    return data["abi"], "0x" + data["bin"]


@pytest.fixture()
def env(compiled):
    abi, bin_ = compiled
    w3 = Web3(EthereumTesterProvider())
    tester = w3.provider.ethereum_tester

    agent_key = Account.from_key("0x" + "33" * 32)
    other_key = Account.from_key("0x" + "44" * 32)
    tester.add_account("0x" + "33" * 32)
    tester.add_account("0x" + "44" * 32)
    genesis = w3.eth.accounts[0]

    for addr in (agent_key.address, other_key.address):
        tester.send_transaction({"from": genesis, "to": addr,
                                 "gas": 21000, "value": 10**18})

    contract = w3.eth.contract(abi=abi, bytecode=bin_)
    receipt = w3.eth.wait_for_transaction_receipt(
        contract.constructor().transact({"from": agent_key.address}))
    audit = w3.eth.contract(address=receipt["contractAddress"], abi=abi)

    audit.functions.setRiskParams(
        5000_00000000, 500_00000000, 50000, 7000,
    ).transact({"from": agent_key.address})
    audit.functions.setRiskParams(
        5000_00000000, 500_00000000, 50000, 7000,
    ).transact({"from": other_key.address})

    return {"w3": w3, "audit": audit,
            "agent": agent_key.address, "other": other_key.address,
            "agent_key": agent_key, "other_key": other_key}


def _log_decision(agent_key, decision_id: bytes, asset: str, size_usd: int):
    confidence = 8500
    entry_price = 50000_00000000
    risk_hash = bytes.fromhex("22" * 32)
    # Mirrors src/audit_logger.py::_compute_payload_hash; asset string is
    # part of the signed payload, so per-asset caps cannot be dodged by
    # relabeling (a relabeled decision has a different hash/signature).
    payload_hash = keccak(b"".join([
        decision_id, b"\x00" * 32, bytes.fromhex(agent_key.address[2:]),
        asset.encode(), b"LONG", b"mean_reversion",
        confidence.to_bytes(32, "big", signed=True),
        entry_price.to_bytes(32, "big"), size_usd.to_bytes(32, "big"),
        risk_hash,
    ]))
    sig = agent_key.sign_message(encode_defunct(primitive=payload_hash)).signature
    return {
        "decisionId": decision_id, "packageId": b"\x00" * 32,
        "asset": asset, "signal": "LONG", "strategy": "mean_reversion",
        "confidence": confidence, "entryPrice": entry_price, "sizeUsd": size_usd,
        "riskHash": risk_hash, "signature": sig, "isShort": False,
    }


def _log(audit, agent_key, agent, asset, size_usd, tag):
    audit.functions.logDecision(
        _log_decision(agent_key, keccak(text=tag), asset, size_usd)
    ).transact({"from": agent})


def _asset_hash(asset: str) -> bytes:
    return keccak(text=asset)


def test_cap_enforced_at_boundary(env):
    """Decisions within the per-asset cap log fine; the first dollar over
    reverts with ASSET_CAP_EXCEEDED — the chain blocks what the off-chain
    gate might let through."""
    audit = env["audit"]
    audit.functions.setAssetCap(BTC, 2000_00000000).transact({"from": env["agent"]})

    _log(audit, env["agent_key"], env["agent"], BTC, 1000_00000000, "cap_a1")
    _log(audit, env["agent_key"], env["agent"], BTC, 1000_00000000, "cap_a2")  # == cap

    with pytest.raises(Exception):
        _log(audit, env["agent_key"], env["agent"], BTC, 1, "cap_a3_over")

    vol, cap = audit.functions.getAssetDailyStats(
        env["agent"], _asset_hash(BTC)).call()
    assert vol == 2000_00000000
    assert cap == 2000_00000000


def test_uncapped_asset_uses_global_checks_only(env):
    """Backward compatible: an asset with no cap set logs under the global
    checks exactly as before (no behavior change for unset assets)."""
    audit = env["audit"]
    _log(audit, env["agent_key"], env["agent"], ETH, 4000_00000000, "nocap_e1")
    vol, cap = audit.functions.getAssetDailyStats(
        env["agent"], _asset_hash(ETH)).call()
    assert vol == 4000_00000000  # accumulator still tracks
    assert cap == 0  # unset


def test_cap_tightening_only(env):
    """A set cap can move down but never up -- and never back to 0
    (disabling the breaker is loosening). Same trust model as setRiskParams."""
    audit = env["audit"]
    audit.functions.setAssetCap(BTC, 2000_00000000).transact({"from": env["agent"]})
    audit.functions.setAssetCap(BTC, 1000_00000000).transact({"from": env["agent"]})

    with pytest.raises(Exception):
        audit.functions.setAssetCap(BTC, 1500_00000000).transact({"from": env["agent"]})
    with pytest.raises(Exception):
        audit.functions.setAssetCap(BTC, 0).transact({"from": env["agent"]})
    with pytest.raises(Exception):
        audit.functions.setAssetCap(ETH, 0).transact({"from": env["agent"]})

    _, cap = audit.functions.getAssetDailyStats(
        env["agent"], _asset_hash(BTC)).call()
    assert cap == 1000_00000000


def test_caps_isolated_per_asset_and_per_agent(env):
    """A BTC cap constrains neither ETH for the same agent nor BTC for
    another agent."""
    audit = env["audit"]
    audit.functions.setAssetCap(BTC, 1000_00000000).transact({"from": env["agent"]})

    _log(audit, env["agent_key"], env["agent"], BTC, 1000_00000000, "iso_b1")
    with pytest.raises(Exception):
        _log(audit, env["agent_key"], env["agent"], BTC, 1000_00000000, "iso_b2")

    # Same agent, different asset: unaffected.
    _log(audit, env["agent_key"], env["agent"], ETH, 1000_00000000, "iso_e1")
    # Different agent, same asset: unaffected (no cap set for them).
    _log(audit, env["other_key"], env["other"], BTC, 1000_00000000, "iso_o1")
