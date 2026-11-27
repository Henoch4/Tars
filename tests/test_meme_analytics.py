from src.meme_analytics import classify_outcome, join_trades, measure_window


def test_classification_is_deterministic():
    assert classify_outcome("RUG_LP_DRAIN") == "rug"
    assert classify_outcome("PIVOT_DROP") == "pivot"
    assert classify_outcome("TIME_BOX") == "time_box"
    assert classify_outcome(None) == "other"


def test_window_metrics_join_entries_and_exits():
    rows = [
        {"event": "screen", "rejected_by": "unconfirmed", "ts": 1},
        {"event": "entry", "mint": "M", "ts": 2},
        {"event": "exit", "mint": "M", "reason": "PIVOT_DROP", "net_usd": 2.5, "ts": 3},
        {"event": "entry_blocked", "mint": "N", "ts": 4},
    ]
    metrics = measure_window(rows)
    assert metrics.joined == 1
    assert metrics.net_usd == 2.5
    assert metrics.rejection_mix == {"unconfirmed": 1}
    assert metrics.outcome_mix == {"pivot": 1}


def test_unmatched_exit_is_not_falsely_joined():
    assert join_trades([{"event": "exit", "mint": "unknown"}]) == []
