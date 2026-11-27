import time
from datetime import datetime, timezone

import pytest

from src.gta_book import GtaBook
from src.gta_event import GtaEventPolicy


def _policy(enabled=True):
    return GtaEventPolicy(
        enabled=enabled,
        live_enabled=True,
        start_utc=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end_utc=datetime(2030, 1, 1, tzinfo=timezone.utc),
        max_total_usd=9,
        max_per_token_usd=3,
        max_open_positions=3,
    )


def _candidate(mint="m1"):
    return {"mint": mint, "symbol": "GTA6", "entry_price": 1.0, "size_usd": 3.0}


def test_book_requires_risk_authorization_before_recording_entry():
    book = GtaBook(_policy(enabled=False))
    with pytest.raises(ValueError, match="event policy disabled"):
        book.record_entry(_candidate())


def test_book_lifecycle_keeps_state_until_exit_fill_ack():
    book = GtaBook(_policy())
    pos = book.record_entry({**_candidate(), "token_amount_raw": 1000}, now_ts=time.time())
    assert pos.mint in book.positions
    decision = book.exit_decision("m1", 2.1)
    assert decision.action == "sell_partial"
    assert "take-profit" in decision.reason
    assert "m1" in book.positions
    book.acknowledge_partial_exit("m1", remaining_raw=500, net_usd=0.1)
    assert book.positions["m1"].token_amount_raw == 500
    book.acknowledge_exit("m1", net_usd=1.0)
    assert book.status()["open_positions"] == 0
