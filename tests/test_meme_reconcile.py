"""Reconciliation tests — pure comparison, no chain."""
from src.meme_reconcile import reconcile, render_reconciliation


def test_all_matched():
    rep = reconcile({"A": {}, "B": {}}, {"A": 1.0, "B": 2.0})
    assert rep["ok"] is True
    assert rep["counts"]["matched"] == 2
    assert rep["alerts"] == []


def test_vanished_position():
    rep = reconcile({"A": {}, "B": {}}, {"A": 1.0})
    assert rep["ok"] is False
    assert rep["vanished"] == ["B"]
    assert any(a["kind"] == "vanished" for a in rep["alerts"])


def test_orphan_holding():
    rep = reconcile({"A": {}}, {"A": 1.0, "Z": 9.0})
    assert rep["ok"] is False
    assert rep["orphan"] == ["Z"]
    assert rep["orphan_balances"]["Z"] == 9.0
    assert "MISMATCH" in render_reconciliation(rep)


def test_dust_and_garbage_ignored():
    rep = reconcile({"A": {}}, {"A": 0.0, "B": float("nan"), "C": "x", "D": 1e-12})
    assert rep["vanished"] == ["A"]
    assert rep["orphan"] == []


def test_empty_both_sides_ok():
    rep = reconcile({}, {})
    assert rep["ok"] is True
    assert rep["counts"] == {"local": 0, "onchain": 0, "matched": 0,
                             "vanished": 0, "orphan": 0}
