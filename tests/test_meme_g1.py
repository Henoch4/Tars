"""G1 report tests — pure blotter reads, no loop, no network."""
import json

from src.meme_g1 import (G1_MIN_DAYS, G1_MIN_ROUND_TRIPS, build_g1_report,
                         load_records, render_g1_report)

DAY = 86400.0
T0 = 1_700_000_000.0


def _write(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_empty_blotter_not_started(tmp_path):
    p = tmp_path / "blotter.jsonl"
    _write(p, [])
    rep = build_g1_report(p, now=T0)
    assert rep["first_entry_ts"] is None
    assert rep["g1_pass"] is False
    assert rep["round_trips"] == 0
    assert "NOT STARTED" in render_g1_report(rep)


def test_load_records_skips_corrupt_lines(tmp_path):
    p = tmp_path / "b.jsonl"
    p.write_text('{"event":"entry","mint":"A"}\n\nnot json\n{"event":"exit"}\n')
    recs = load_records(p)
    assert [r.get("event") for r in recs] == ["entry", "exit"]


def test_full_g1_pass(tmp_path):
    p = tmp_path / "b.jsonl"
    recs = [{"event": "entry", "mint": f"M{i}", "ts": T0} for i in range(G1_MIN_ROUND_TRIPS)]
    # 30 exits, each +$2 -> net +60, all within the window
    recs += [{"event": "exit", "mint": f"M{i}", "net_usd": 2.0, "ts": T0 + 100}
             for i in range(G1_MIN_ROUND_TRIPS)]
    _write(p, recs)
    rep = build_g1_report(p, now=T0 + (G1_MIN_DAYS + 1) * DAY)
    assert rep["round_trips"] == G1_MIN_ROUND_TRIPS
    assert rep["unique_mints"] == G1_MIN_ROUND_TRIPS
    assert rep["net_usd"] == 60.0
    assert rep["days_ok"] and rep["count_ok"] and rep["net_ok"]
    assert rep["g1_pass"] is True


def test_g1_blocked_by_short_clock(tmp_path):
    p = tmp_path / "b.jsonl"
    recs = [{"event": "entry", "mint": f"M{i}", "ts": T0} for i in range(G1_MIN_ROUND_TRIPS)]
    recs += [{"event": "exit", "mint": f"M{i}", "net_usd": 1.0} for i in range(G1_MIN_ROUND_TRIPS)]
    _write(p, recs)
    rep = build_g1_report(p, now=T0 + 2 * DAY)
    assert rep["count_ok"] and rep["net_ok"]
    assert rep["days_ok"] is False
    assert rep["g1_pass"] is False


def test_g1_blocked_by_negative_net(tmp_path):
    p = tmp_path / "b.jsonl"
    recs = [{"event": "entry", "mint": f"M{i}", "ts": T0} for i in range(G1_MIN_ROUND_TRIPS)]
    recs += [{"event": "exit", "mint": f"M{i}", "net_usd": -1.0} for i in range(G1_MIN_ROUND_TRIPS)]
    _write(p, recs)
    rep = build_g1_report(p, now=T0 + 10 * DAY)
    assert rep["net_ok"] is False and rep["g1_pass"] is False


def test_funnel_and_cooldown_blocks(tmp_path):
    p = tmp_path / "b.jsonl"
    _write(p, [
        {"event": "screen", "mint": "A", "passed": True, "ts": T0},
        {"event": "screen", "mint": "B", "passed": False, "rejected_by": "thin_liquidity"},
        {"event": "screen", "mint": "C", "passed": False, "rejected_by": "thin_liquidity"},
        {"event": "entry_blocked", "mint": "A", "reason": "re-entry cooldown"},
        {"event": "entry_blocked", "mint": "A", "reason": "session cap"},
    ])
    rep = build_g1_report(p, now=T0)
    assert rep["funnel"]["passed"] == 1
    assert rep["funnel"]["thin_liquidity"] == 2
    assert rep["cooldown_blocks"] == 1


def test_positions_net_crosscheck(tmp_path):
    p = tmp_path / "b.jsonl"
    _write(p, [{"event": "entry", "mint": "A", "ts": T0},
               {"event": "exit", "mint": "A", "net_usd": 5.0}])
    pos = tmp_path / "positions.json"
    pos.write_text(json.dumps({"realized_net_usd": 5.0}))
    rep = build_g1_report(p, positions_path=pos, now=T0)
    assert rep["positions_net_usd"] == 5.0


def test_cli_runs_and_reports(tmp_path, capsys):
    """scripts/meme_g1_report.py must run read-only and print the block."""
    from scripts.meme_g1_report import main
    p = tmp_path / "b.jsonl"
    _write(p, [{"event": "entry", "mint": "A", "ts": T0},
               {"event": "exit", "mint": "A", "net_usd": 5.0}])
    rc = main(["--blotter", str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "G1 progress" in out and "round-trips: 1 / 30" in out


def test_cli_json_mode(tmp_path, capsys):
    from scripts.meme_g1_report import main
    p = tmp_path / "b.jsonl"
    _write(p, [{"event": "entry", "mint": "A", "ts": T0}])
    assert main(["--blotter", str(p), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["entries"] == 1
