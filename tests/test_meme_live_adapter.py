from dataclasses import dataclass

from src.meme_live_adapter import SinglePositionReconciler


@dataclass
class Position:
    mint: str = "mint"
    token_amount: float = 1.25
    decimals: int = 6


def test_reconciler_matches_exact_raw_units():
    reconciler = SinglePositionReconciler(lambda: Position(), lambda _m, _o: 1_250_000, "owner")
    report = reconciler()
    assert report["ok"] is True
    assert report["positions"][0]["observed_raw"] == 1_250_000


def test_reconciler_halts_on_mismatch():
    reconciler = SinglePositionReconciler(lambda: Position(), lambda _m, _o: 1_249_999, "owner")
    report = reconciler()
    assert report["ok"] is False
    assert report["reason"] == "raw balance mismatch"


def test_reconciler_fails_closed_on_reader_error():
    def broken(_m, _o):
        raise RuntimeError("rpc down")
    report = SinglePositionReconciler(lambda: Position(), broken, "owner")()
    assert report["ok"] is False
    assert report["reason"] == "balance reader failed"


def test_reconciler_accepts_empty_book():
    assert SinglePositionReconciler(lambda: None, lambda _m, _o: 0, "owner")()["ok"] is True
