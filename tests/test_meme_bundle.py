"""Bundle-graph tests: clustering, window, fail-closed budget.
Transport faked (no network, no key)."""
import base64
import struct

import pytest

from src.meme_bundle import BundleError, BundleGraph


def _mint_b64(supply=1_000_000_000_000, decimals=6):
    raw = (struct.pack("<I", 0) + b"\x11" * 32
           + struct.pack("<Q", supply) + bytes([decimals, 1])
           + struct.pack("<I", 0) + b"\x22" * 32)
    return base64.b64encode(raw).decode()


def _tx(transfers, block_time=1000, slot=7):
    ixs = [{"program": "system",
            "parsed": {"type": "transfer",
                       "info": {"source": s, "destination": d,
                                "lamports": 10_000_000}}}
           for s, d in transfers]
    return {"slot": slot, "blockTime": block_time,
            "transaction": {"message": {"instructions": ixs}},
            "meta": {"err": None, "innerInstructions": []}}


class _FakeNet:
    """Method-routed fake transport. sigs: addr -> [sig]; txs: sig -> tx."""

    def __init__(self, holders, sigs, txs, supply=None):
        self.holders = holders
        self.sigs = sigs
        self.txs = txs
        self.supply = supply
        self.calls = []

    def __call__(self, payload):
        method, params = payload["method"], payload["params"]
        self.calls.append(method)
        if method == "getTokenLargestAccounts":
            return {"value": [{"address": a, "amount": str(n)}
                              for a, n in self.holders]}
        if method == "getAccountInfo":
            return {"value": {"data": [self.supply or _mint_b64(), "base64"]}}
        if method == "getSignaturesForAddress":
            addr = params[0]
            return [{"signature": s, "slot": 1, "err": None,
                     "blockTime": 1000}
                    for s in self.sigs.get(addr, [])]
        if method == "getTransaction":
            return self.txs[params[0]]
        raise AssertionError(f"unexpected {method}")


def _graph(net, **kw):
    return BundleGraph(rpc_url="http://fake", post=net, **kw)


def test_shared_distributor_clusters():
    # D funds H1 (30%) + H2 (20%) within minutes: one 50% cluster.
    net = _FakeNet(
        [("H1", 300_000_000_000), ("H2", 200_000_000_000),
         ("H3", 100_000_000_000)],
        {"H1": ["s1"], "H2": ["s2"], "H3": ["s3"]},
        {"s1": _tx([("D", "H1")], block_time=1000),
         "s2": _tx([("D", "H2")], block_time=1100),
         "s3": _tx([("E", "H3")], block_time=1200)})
    out = _graph(net).clusters("MINT")
    assert out["holders_examined"] == 3
    assert len(out["clusters"]) == 1
    c = out["clusters"][0]
    assert c["ancestor"] == "D"
    assert sorted(c["members"]) == ["H1", "H2"]
    assert c["share_pct"] == pytest.approx(50.0)
    assert out["max_cluster_share_pct"] == pytest.approx(50.0)
    assert out["flag"] is True and out["investigate"] is True
    assert out["complete"] is True


def test_independent_funders_no_cluster():
    net = _FakeNet(
        [("H1", 300_000_000_000), ("H2", 200_000_000_000)],
        {"H1": ["s1"], "H2": ["s2"]},
        {"s1": _tx([("D1", "H1")], block_time=1000),
         "s2": _tx([("D2", "H2")], block_time=1100)})
    out = _graph(net).clusters("MINT")
    assert out["clusters"] == []
    assert out["max_cluster_share_pct"] == 0.0
    assert out["flag"] is False


def test_window_split_breaks_cluster():
    net = _FakeNet(
        [("H1", 300_000_000_000), ("H2", 200_000_000_000)],
        {"H1": ["s1"], "H2": ["s2"]},
        {"s1": _tx([("D", "H1")], block_time=1000),
         "s2": _tx([("D", "H2")], block_time=1000 + 7200)})
    out = _graph(net, window_s=1800).clusters("MINT")
    assert out["clusters"] == []


def test_single_holder_never_clusters():
    net = _FakeNet([("H1", 500_000_000_000)], {"H1": ["s1"]},
                   {"s1": _tx([("D", "H1")])})
    assert _graph(net).clusters("MINT")["clusters"] == []


def test_empty_holders_raises():
    net = _FakeNet([], {}, {})
    with pytest.raises(BundleError):
        _graph(net).clusters("MINT")


def test_transport_error_is_typed():
    def boom(payload):
        raise ConnectionError("down")
    with pytest.raises(BundleError):
        _graph(boom).clusters("MINT")


def test_fetch_budget_marks_incomplete_never_clean(monkeypatch):
    # Throttled walks must report incomplete, NOT "no bundles found":
    # absence of evidence is not evidence of independence.
    import src.meme_bundle as bundle
    net = _FakeNet(
        [(f"H{i}", 10_000_000_000) for i in range(6)],
        {f"H{i}": [f"s{i}"] for i in range(6)},
        {f"s{i}": _tx([("D", f"H{i}")]) for i in range(6)})
    monkeypatch.setattr(bundle, "MAX_TX_FETCHES", 3)
    out = _graph(net).clusters("MINT")
    assert out["complete"] is False
    assert out["clusters"] == []
    assert len(out["skipped_holders"]) == 6


def test_inner_instructions_count():
    tx = {"slot": 1, "blockTime": 1000,
          "transaction": {"message": {"instructions": []}},
          "meta": {"err": None, "innerInstructions": [
              {"instructions": [
                  {"program": "system",
                   "parsed": {"type": "transfer",
                              "info": {"source": "D", "destination": "H1",
                                       "lamports": 5}}}]}]}}
    net = _FakeNet([("H1", 500_000_000_000)], {"H1": ["s1"]}, {"s1": tx})
    g = _graph(net)
    assert g.funding_inflows("H1") == [{"source": "D", "slot": 1,
                                       "time": 1000}]
