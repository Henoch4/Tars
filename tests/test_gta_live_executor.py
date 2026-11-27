from datetime import datetime, timezone

from src.gta_book import GtaBook
from src.gta_event import GtaEventPolicy
from src.gta_live_executor import GtaLiveExecutor
from src.meme_execute import ExecutionLedger


def _policy():
    return GtaEventPolicy(
        enabled=True,
        live_enabled=True,
        start_utc=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end_utc=datetime(2030, 1, 1, tzinfo=timezone.utc),
        max_total_usd=9,
        max_per_token_usd=3,
        max_open_positions=3,
    )


def _candidate():
    return {"mint": "m1", "symbol": "GTA6", "entry_price": 1.0, "size_usd": 3.0}


def test_live_adapter_requires_confirm_before_calling_buy():
    calls = []

    def buy(*args, **kwargs):
        calls.append((args, kwargs))
        return {"broadcast": True}

    executor = GtaLiveExecutor(
        GtaBook(_policy()), keypath="unused", ledger=ExecutionLedger(),
        buy_fn=buy, pubkey_fn=lambda _path: "public",
    )
    result = executor.enter(_candidate(), sol_price_usd=100, confirm="")
    assert result["broadcast"] is False
    assert calls == []


def test_live_adapter_records_state_only_after_confirmed_buy_and_exit():
    def buy(*args, **kwargs):
        return {"broadcast": True, "receipt": {"signature": "buy"}}

    def sell(*args, **kwargs):
        return {"broadcast": True, "receipt": {"signature": "sell"}}

    executor = GtaLiveExecutor(
        GtaBook(_policy()), keypath="unused", ledger=ExecutionLedger(),
        buy_fn=buy, sell_fn=sell, pubkey_fn=lambda _path: "public",
        balance_reader=lambda _mint: 0,
    )
    candidate = {**_candidate(), "token_amount_raw": 1000}
    bought = executor.enter(candidate, sol_price_usd=100, confirm="GO")
    assert bought["broadcast"] is True
    assert "m1" in executor.book.positions
    sold = executor.exit("m1", amount_raw=1000, confirm="SELL", net_usd=0.1)
    assert sold["broadcast"] is True
    assert "m1" not in executor.book.positions


def test_live_adapter_requires_post_fill_reader_for_exit():
    def buy(*args, **kwargs):
        return {"broadcast": True}

    def sell(*args, **kwargs):
        return {"broadcast": True}

    executor = GtaLiveExecutor(
        GtaBook(_policy()), keypath="unused", ledger=ExecutionLedger(),
        buy_fn=buy, sell_fn=sell, pubkey_fn=lambda _path: "public",
    )
    executor.enter({**_candidate(), "token_amount_raw": 1000}, sol_price_usd=100, confirm="GO")
    result = executor.exit("m1", confirm="SELL")
    assert result["broadcast"] is False
    assert "post-fill balance" in result["reason"]
