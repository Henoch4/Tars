from dataclasses import dataclass

from src.meme_exit_adapter import MainSatelliteExitAdapter


@dataclass
class Position:
    mint: str = "mint"
    token_amount: float = 2.0
    decimals: int = 6
    sell_executed: bool = False


def test_exit_sizes_from_chain_and_closes_after_zero():
    pos = Position()
    balances = iter([2_000_000, 0])
    calls = []
    adapter = MainSatelliteExitAdapter(lambda: pos, lambda p: None,
        lambda _m, _o: next(balances), "owner",
        lambda **kwargs: calls.append(kwargs) or {"signature": "sig"})
    result = adapter.exit(confirm="CONFIRM")
    assert result.closed is True
    assert calls[0]["amount_raw"] == 2_000_000
    assert pos.sell_executed is True


def test_exit_preserves_residual_dust():
    pos = Position()
    balances = iter([2_000_000, 123])
    adapter = MainSatelliteExitAdapter(lambda: pos, lambda p: None,
        lambda _m, _o: next(balances), "owner", lambda **_: {})
    result = adapter.exit(confirm="CONFIRM")
    assert result.closed is False
    assert result.remaining_raw == 123
    assert pos.sell_executed is False
    assert pos.token_amount == 0.000123


def test_exit_requires_reader_and_confirmation():
    pos = Position()
    adapter = MainSatelliteExitAdapter(lambda: pos, lambda p: None, None,
        "owner", lambda **_: {})
    assert adapter.exit(confirm="CONFIRM").reason == "balance reader unavailable"
    adapter = MainSatelliteExitAdapter(lambda: pos, lambda p: None,
        lambda _m, _o: 2_000_000, "owner", lambda **_: {})
    assert adapter.exit().reason == "live confirmation required"


def test_flatten_reports_unresolved_sell_failure():
    pos = Position()
    adapter = MainSatelliteExitAdapter(lambda: pos, lambda p: None,
        lambda _m, _o: 2_000_000, "owner",
        lambda **_: (_ for _ in ()).throw(RuntimeError("rpc")))
    report = adapter.flatten(confirm="CONFIRM")
    assert report["unresolved"] == ["mint"]
