"""Deployer-history bootstrap tests: aggregation over blotter screen rows.
Hermetic (synthetic blotter files)."""
import json

from src.meme_history import deployer_stats, serial_deployers


def _write(path, events):
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _screen(mint, creator, rugged=None, ts=1000.0):
    return {"event": "screen", "mint": mint, "passed": False,
            "rejected_by": "x", "reasons": [], "ts": ts,
            "creator_address": creator, "rugcheck_rugged": rugged}


def test_counts_distinct_mints_per_creator(tmp_path):
    p = tmp_path / "b.jsonl"
    _write(str(p), [_screen("M1", "C1", False, 1000.0),
                    _screen("M1", "C1", False, 1001.0),  # same mint twice
                    _screen("M2", "C1", True, 1002.0),
                    _screen("M3", "C2", False, 1003.0)])
    stats = deployer_stats(str(p))
    assert stats["C1"]["launches"] == 2
    assert stats["C1"]["rugged_seen"] == 1
    assert stats["C1"]["first_ts"] == 1000.0
    assert stats["C1"]["last_ts"] == 1002.0
    assert stats["C2"]["launches"] == 1


def test_rows_without_creator_skipped_never_guessed(tmp_path):
    p = tmp_path / "b.jsonl"
    _write(str(p), [{"event": "screen", "mint": "M9", "ts": 1.0},
                    {"event": "entry", "mint": "M9",
                     "creator_address": "C9"}])
    assert deployer_stats(str(p)) == {}


def test_missing_file_is_empty(tmp_path):
    assert deployer_stats(str(tmp_path / "nope.jsonl")) == {}
    assert serial_deployers(str(tmp_path / "nope.jsonl")) == []


def test_serial_threshold_and_order(tmp_path):
    p = tmp_path / "b.jsonl"
    evs = [_screen(f"A{i}", "BIG", False, 1000.0 + i) for i in range(4)]
    evs += [_screen(f"B{i}", "MID", False, 2000.0 + i) for i in range(3)]
    evs += [_screen("C0", "SMALL", False, 3000.0)]
    _write(str(p), evs)
    rows = serial_deployers(str(p), min_launches=3)
    assert [(c, n) for c, n, _ in rows] == [("BIG", 4), ("MID", 3)]


def test_rebuild_writes_atomic_file_and_lookup(tmp_path):
    from src.meme_history import load_history, rebuild_history, rugs_for_creator
    blotter = tmp_path / "b.jsonl"
    _write(str(blotter), [_screen("M1", "C1", True, 1000.0),
                          _screen("M2", "C1", False, 1001.0),
                          _screen("M3", "C2", False, 1002.0)])
    out = tmp_path / "hist.json"
    stats = rebuild_history(str(blotter), str(out))
    assert stats["C1"]["rugged_seen"] == 1
    assert out.exists() and not (tmp_path / "hist.json.tmp").exists()
    hist = load_history(str(out))
    assert hist["deployer_count"] == 2
    assert rugs_for_creator(hist, "C1") == 1
    assert rugs_for_creator(hist, "C2") == 0
    assert rugs_for_creator(hist, "UNKNOWN") == 0  # cold-start: clean
    assert rugs_for_creator(hist, None) == 0
    # cached second read
    assert load_history(str(out))["deployer_count"] == 2


def test_load_missing_is_empty(tmp_path):
    from src.meme_history import load_history
    assert load_history(str(tmp_path / "nope.json")) == {}
