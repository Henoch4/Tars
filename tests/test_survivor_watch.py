"""Survivor observer tests: enroll graduates only, cap weekly, snapshot
appends, loop state untouched. Hermetic (fake blotter + fake enrich)."""
import json
import time

import scripts.meme_survivor_watch as watch


def _blotter(path, events):
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _screen(mint, graduated, cycle=1):
    reasons = ["25 holders visible"]
    if graduated:
        reasons.append("survival confirmed: structural entry allowed")
    else:
        reasons.append("no survival confirmation (graduation/liquidity floor)")
    return {"event": "screen", "mint": mint, "passed": graduated,
            "rejected_by": None if graduated else "unconfirmed",
            "reasons": reasons, "cycle": cycle}


def test_enroll_picks_only_graduates(tmp_path, monkeypatch, capsys):
    import src.meme_loop as ml
    blotter = tmp_path / "blotter.jsonl"
    _blotter(str(blotter), [_screen("G1", True), _screen("N1", False),
                            {"event": "entry", "mint": "G1"}])
    monkeypatch.setattr(ml, "BLOTTER_PATH", str(blotter))
    added = watch.enroll(now=1_800_000_000.0)
    assert added == ["G1"]
    out = capsys.readouterr().out
    assert "enrolled 1" in out


def test_enroll_caps_five_per_week_and_idempotent(tmp_path, monkeypatch):
    import src.meme_loop as ml
    blotter = tmp_path / "blotter.jsonl"
    _blotter(str(blotter), [_screen(f"G{i}", True) for i in range(8)])
    monkeypatch.setattr(ml, "BLOTTER_PATH", str(blotter))
    assert len(watch.enroll(now=1_800_000_000.0)) == 5
    assert watch.enroll(now=1_800_000_100.0) == []  # cap hit + idempotent
    # next week the remaining 3 enroll
    assert len(watch.enroll(now=1_800_000_000.0 + 8 * 86400)) == 3


def test_snapshot_appends_rows_and_records_errors(tmp_path, monkeypatch,
                                                  capsys):
    import src.meme_loop as ml
    from src.meme_discovery import DiscoveryError
    blotter = tmp_path / "blotter.jsonl"
    _blotter(str(blotter), [_screen("G1", True), _screen("G2", True)])
    monkeypatch.setattr(ml, "BLOTTER_PATH", str(blotter))
    watch.enroll(now=1_800_000_000.0)

    def fake_enrich(mint):
        if mint == "G2":
            raise DiscoveryError("too fresh to index?")
        return {"price_usd": 0.5, "liquidity_usd": 60000.0,
                "volume_24h_usd": 9000.0, "venue": "meteora",
                "graduated": True}

    n_ok, n_err = watch.snapshot(now=1_800_001_000.0, enrich=fake_enrich)
    assert (n_ok, n_err) == (1, 1)
    rows = (tmp_path / "survivors" / "G1.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["price_usd"] == 0.5 and row["venue"] == "meteora"
    err_row = json.loads((tmp_path / "survivors" / "G2.jsonl").read_text(
        encoding="utf-8").strip())
    assert "error" in err_row
    assert "snapshot: 1 ok, 1 errors" in capsys.readouterr().out


def test_observer_never_touches_loop_state(tmp_path, monkeypatch):
    import src.meme_loop as ml
    blotter = tmp_path / "blotter.jsonl"
    positions = tmp_path / "positions.json"
    _blotter(str(blotter), [_screen("G1", True)])
    positions.write_text('{"open": []}', encoding="utf-8")
    before = (blotter.read_bytes(), positions.read_bytes())
    monkeypatch.setattr(ml, "BLOTTER_PATH", str(blotter))
    watch.enroll(now=1_800_000_000.0)
    watch.snapshot(now=1_800_001_000.0,
                   enrich=lambda m: {"price_usd": 1.0})
    assert blotter.read_bytes() == before[0]
    assert positions.read_bytes() == before[1]
    # only survivors/ gained files
    assert sorted(p.name for p in tmp_path.iterdir()) == \
        ["blotter.jsonl", "positions.json", "survivors"]
