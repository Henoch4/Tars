"""TODO 5.6: on-chain agent registry (eth-tester EVM).

manifest.json is static — no reputation, pricing, or discovery index.
AgentRegistry.sol gives agents a self-sovereign record (endpoint +
capabilities hash + price-tiers version, keyed by msg.sender so the act of
transacting is the auth) while reputation stays a VIEW over
TradeAuditTrail.getAgentDailyStats: derived from real trading history,
unfakeable except by actually trading.

Compiling the real contract here (py-solc-x via-ir) doubles as the
recompile verification for the Solidity addition.
"""
from pathlib import Path

import pytest
from eth_account import Account
from eth_utils import keccak
from solcx import compile_source
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

SOLC_VERSION = "0.8.30"


def _compile(name):
    path = (Path(__file__).resolve().parent.parent / "contracts"
            / "contracts" / name)
    result = compile_source(path.read_text(encoding="utf-8"),
                            solc_version=SOLC_VERSION,
                            output_values=["abi", "bin"], via_ir=True)
    data = result.get(f"<stdin>:{name[:-4]}")
    if data is None:
        raise KeyError(f"{name} not found in compile output")
    return data["abi"], "0x" + data["bin"]


@pytest.fixture(scope="session")
def compiled():
    audit_abi, audit_bin = _compile("TradeAuditTrail.sol")
    reg_abi, reg_bin = _compile("AgentRegistry.sol")
    return {"audit": (audit_abi, audit_bin), "reg": (reg_abi, reg_bin)}


@pytest.fixture()
def env(compiled):
    w3 = Web3(EthereumTesterProvider())
    tester = w3.provider.ethereum_tester
    agent_key = Account.from_key("0x" + "55" * 32)
    other_key = Account.from_key("0x" + "66" * 32)
    tester.add_account("0x" + "55" * 32)
    tester.add_account("0x" + "66" * 32)
    genesis = w3.eth.accounts[0]
    for addr in (agent_key.address, other_key.address):
        tester.send_transaction({"from": genesis, "to": addr,
                                 "gas": 21000, "value": 10 ** 18})

    audit_abi, audit_bin = compiled["audit"]
    audit = w3.eth.contract(abi=audit_abi, bytecode=audit_bin)
    audit_addr = w3.eth.wait_for_transaction_receipt(
        audit.constructor().transact(
            {"from": agent_key.address}))["contractAddress"]
    audit_c = w3.eth.contract(address=audit_addr, abi=audit_abi)

    reg_abi, reg_bin = compiled["reg"]
    reg = w3.eth.contract(abi=reg_abi, bytecode=reg_bin)
    reg_addr = w3.eth.wait_for_transaction_receipt(
        reg.constructor(audit_addr).transact(
            {"from": agent_key.address}))["contractAddress"]
    reg_c = w3.eth.contract(address=reg_addr, abi=reg_abi)
    return {"w3": w3, "reg": reg_c, "audit": audit_c,
            "reg_abi": reg_abi, "reg_bin": reg_bin,
            "agent": agent_key.address, "other": other_key.address}


CAPS = keccak(text="hire,trade")
EP = "https://agent.example.com"


def test_register_makes_agent_queryable(env):
    reg = env["reg"]
    reg.functions.register(EP, CAPS, "v1").transact({"from": env["agent"]})
    rec = reg.functions.agents(env["agent"]).call()
    assert rec[0] == EP and rec[1] == CAPS and rec[2] == "v1"
    assert rec[4] is True
    assert reg.functions.isActive(env["agent"]).call() is True


def test_double_register_reverts(env):
    reg = env["reg"]
    reg.functions.register(EP, CAPS, "v1").transact({"from": env["agent"]})
    with pytest.raises(Exception):
        reg.functions.register(EP, CAPS, "v1").transact({"from": env["agent"]})


def test_empty_endpoint_reverts(env):
    reg = env["reg"]
    with pytest.raises(Exception):
        reg.functions.register("", CAPS, "v1").transact({"from": env["agent"]})


def test_constructor_rejects_zero_audit_trail(env):
    w3 = env["w3"]
    factory = w3.eth.contract(abi=env["reg_abi"], bytecode=env["reg_bin"])
    with pytest.raises(Exception):
        factory.constructor(
            "0x0000000000000000000000000000000000000000").transact(
                {"from": env["agent"]})


def test_update_rotates_record(env):
    reg = env["reg"]
    reg.functions.register(EP, CAPS, "v1").transact({"from": env["agent"]})
    reg.functions.update("https://new.example.com", keccak(text="hire"),
                         "v2").transact({"from": env["agent"]})
    rec = reg.functions.agents(env["agent"]).call()
    assert rec[0] == "https://new.example.com" and rec[2] == "v2"


def test_update_when_inactive_reverts(env):
    reg = env["reg"]
    with pytest.raises(Exception):
        reg.functions.update(EP, CAPS, "v1").transact({"from": env["other"]})


def test_deactivate_delists_but_retains_record(env):
    reg = env["reg"]
    reg.functions.register(EP, CAPS, "v1").transact({"from": env["agent"]})
    reg.functions.deactivate().transact({"from": env["agent"]})
    assert reg.functions.isActive(env["agent"]).call() is False
    rec = reg.functions.agents(env["agent"]).call()
    assert rec[0] == EP  # history retained, just inactive
    with pytest.raises(Exception):
        reg.functions.deactivate().transact({"from": env["agent"]})


def test_reputation_unregistered_agent_is_zeroed(env):
    vol, loss, trades, kill, registered, active = \
        env["reg"].functions.reputation(env["other"]).call()
    assert (vol, loss, trades) == (0, 0, 0)
    assert kill is False
    assert registered is False and active is False


def test_reputation_reflects_audit_trail(env):
    reg, audit = env["reg"], env["audit"]
    reg.functions.register(EP, CAPS, "v1").transact({"from": env["agent"]})
    audit.functions.activateKillSwitch("drill").transact({"from": env["agent"]})
    vol, loss, trades, kill, registered, active = \
        reg.functions.reputation(env["agent"]).call()
    assert registered is True and active is True
    assert kill is True  # derived live from the audit trail, not self-reported
    audit.functions.deactivateKillSwitch().transact({"from": env["agent"]})
    assert reg.functions.reputation(env["agent"]).call()[3] is False
