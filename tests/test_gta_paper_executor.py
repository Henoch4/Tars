import time
from datetime import datetime, timezone

from src.gta_book import GtaBook
from src.gta_event import GtaEventPolicy
from src.gta_paper_executor import GtaPaperExecutor


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


def test_paper_executor_runs_entry_partial_and_final_exit():
    executor = GtaPaperExecutor(GtaBook(_policy()))
    receipt = executor.enter({"mint": "m1", "symbol": "GTA6", "entry_price": 1.0, "size_usd": 3.0}, time.time())
    assert receipt.broadcast is False
    partial = executor.mark("m1", 2.1)
    assert partial is not None and partial.side == "sell"
    assert "m1" in executor.book.positions
    final = executor.mark("m1", 1.0)
    assert final is not None and final.side == "sell"
    assert "m1" not in executor.book.positions
