"""Launch-forensics tests: block-0 clustering, dev-minute-1, fail-soft.
Transport faked (no network, no key)."""
import pytest

from src.meme_launch import LaunchError, LaunchForensics


def _sigtx(sigs, txs):
    def post(payload):
        method = payload["method"]
        if method == "getSignaturesForAddress":
            return sigs
        if method == "getTransaction":
            return txs[payload["params"][0]]
        raise AssertionError(f"unexpected {method}")
    return post


def _tx(slot, signers, transfers=(), t=1000):
    return {"slot": slot, "blockTime": t,
            "transaction": {"message": {"instructions": [],
                                        "accountKeys": [
                                            {"pubkey": s, "signer": True}
                                            for s in signers]}},
            "meta": {"err": None, "innerInstructions": [
                {"instructions": [
                    {"program": "system",
                     "parsed": {"type": "transfer",
                                "info": {"source": s, "destination": d,
                                         "lamports": 1}}}
                    for s, d in transfers]}]}}


def _sigs(n, start=100):
    return [{"signature": f"s{i}", "slot": start, "err": None,
             "blockTime": 1000} for i in range(n)]


def test_block0_counts_distinct_buyers_excluding_creator():
    txs = {"s0": _tx(100, ["DEV", "B1", "B2"], [("X", "B1")], t=1000),
           "s1": _tx(100, ["DEV", "B1", "B3"], [], t=1001),
           "s2": _tx(101, ["LATE"], [], t=2000)}
    g = LaunchForensics(rpc_url="http://fake",
                        post=_sigtx(_sigs(3), txs))
    out = g.examine("MINT", creator="DEV")
    assert out["creation_slot"] == 100
    assert out["block0_txns"] == 2
    assert out["block0_buyers"] == 3  # B1, B2, B3 (DEV excluded)
    assert out["dev_sold_minute1"] is False
    assert out["dev_first_sell_s"] is None


def test_dev_minute1_sell_detected():
    txs = {"s0": _tx(100, ["DEV"], [("DEV", "B1")], t=1000),
           "s1": _tx(101, ["X"], [], t=2000)}
    g = LaunchForensics(rpc_url="http://fake",
                        post=_sigtx(_sigs(2), txs))
    out = g.examine("MINT", creator="DEV")
    assert out["dev_sold_minute1"] is True
    assert out["dev_first_sell_s"] == 0.0


def test_dev_late_sell_not_minute1():
    txs = {"s0": _tx(100, ["DEV"], [], t=1000),
           "s1": _tx(101, ["X"], [("DEV", "B9")], t=1000 + 3600)}
    g = LaunchForensics(rpc_url="http://fake",
                        post=_sigtx(_sigs(2), txs))
    out = g.examine("MINT", creator="DEV")
    assert out["dev_sold_minute1"] is False


def test_unknown_creator_yields_counts_not_verdict():
    txs = {"s0": _tx(100, ["A", "B"], [], t=1000)}
    g = LaunchForensics(rpc_url="http://fake",
                        post=_sigtx(_sigs(1), txs))
    out = g.examine("MINT")
    assert out["block0_buyers"] == 2
    assert out["dev_sold_minute1"] is None


def test_empty_signatures_raises():
    g = LaunchForensics(rpc_url="http://fake",
                        post=_sigtx([], {}))
    with pytest.raises(LaunchError):
        g.examine("MINT")


def test_transport_error_is_typed():
    def boom(payload):
        raise ConnectionError("down")
    with pytest.raises(LaunchError):
        LaunchForensics(rpc_url="http://fake", post=boom).examine("MINT")
