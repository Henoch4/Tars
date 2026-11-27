from datetime import datetime, timezone

from src.gta_book import GtaBook
from src.gta_event import GtaEventPolicy
from src.gta_live_executor import GtaLiveExecutor
from src.meme_execute import ExecutionLedger


def _policy():
    return GtaEventPolicy(
        enabled=True, live_enabled=True,
        start_utc=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end_utc=datetime(2030, 1, 1, tzinfo=timezone.utc),
        max_total_usd=9, max_per_token_usd=3, max_open_positions=3,
    )


def _book():
    book = GtaBook(_policy())
    book.record_entry({"mint": "m1", "symbol": "A", "entry_price": 1.0, "size_usd": 3.0, "token_amount_raw": 100})
    book.record_entry({"mint": "m2", "symbol": "B", "entry_price": 1.0, "size_usd": 3.0, "token_amount_raw": 200})
    return book


def test_flatten_closes_all_positions_after_confirmed_zero_balances():
    reads = {"m1": iter([100, 0]), "m2": iter([200, 0])}
    executor = GtaLiveExecutor(
        _book(), keypath="unused", ledger=ExecutionLedger(),
        sell_fn=lambda *a, **k: {"broadcast": True},
        pubkey_fn=lambda _: "public",
        balance_reader=lambda mint: next(reads[mint]),
    )
    report = executor.flatten_all(confirm="FLATTEN", reason="kill")
    assert report.closed == 2
    assert report.failed == 0
    assert report.residual_mints == ()


def test_flatten_keeps_failed_position_visible():
    executor = GtaLiveExecutor(
        _book(), keypath="unused", ledger=ExecutionLedger(),
        sell_fn=lambda *a, **k: {"broadcast": False, "reason": "slippage"},
        pubkey_fn=lambda _: "public",
        balance_reader=lambda _mint: 100,
    )
    report = executor.flatten_all(confirm="FLATTEN")
    assert report.failed == 2
    assert set(report.residual_mints) == {"m1", "m2"}


def test_flatten_preserves_residual_dust():
    reads = iter([100, 10])
    book = GtaBook(_policy())
    book.record_entry({"mint": "m1", "symbol": "A", "entry_price": 1.0, "size_usd": 3.0, "token_amount_raw": 100})
    executor = GtaLiveExecutor(
        book, keypath="unused", ledger=ExecutionLedger(),
        sell_fn=lambda *a, **k: {"broadcast": True},
        pubkey_fn=lambda _: "public",
        balance_reader=lambda _mint: next(reads),
    )
    report = executor.flatten_all(confirm="FLATTEN")
    assert report.failed == 1
    assert book.positions["m1"].token_amount_raw == 10


def test_flatten_refuses_without_balance_reader():
    executor = GtaLiveExecutor(_book(), keypath="unused", ledger=ExecutionLedger())
    report = executor.flatten_all(confirm="FLATTEN")
    assert report.failed == 1
    assert set(report.residual_mints) == {"m1", "m2"}
