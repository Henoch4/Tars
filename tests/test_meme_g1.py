"""G1 report tests — pure blotter reads, no loop, no network."""
import json

import pytest

from src.meme_g1 import (CLASS_OTHER, CLASS_PIVOT, CLASS_RUG, CLASS_TIME_BOX,
                         G1_MIN_DAYS, G1_MIN_ROUND_TRIPS, build_attribution,
                         build_g1_report, classify_exit_reason, load_records,
                         render_attribution, render_g1_report)

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


# -- attribution -------------------------------------------------------
# Exit reasons are written by meme_satellite.evaluate_exit / check_rug and by
# meme_loop.check_exits. Classification must match the loop's own rug test
# (`str(reason).upper().startswith("RUG")`) so report and behaviour agree.

@pytest.mark.parametrize("reason,expected", [
    ("RUG: LP drained 95% since entry", CLASS_RUG),
    ("RUG: collapsing 18%/min off peak", CLASS_RUG),
    ("rug: lowercase sentinel", CLASS_RUG),  # case-insensitive, like the loop
    ("pivot: -35% off peak (>25%)", CLASS_PIVOT),
    ("Pivot: volume collapsed (dead tape)", CLASS_PIVOT),
    ("time box expired (7d max dwell)", CLASS_TIME_BOX),
    ("TIME BOX EXPIRED (3d max dwell)", CLASS_TIME_BOX),
    ("breaker: daily loss", CLASS_OTHER),
    ("", CLASS_OTHER),
    (None, CLASS_OTHER),
    (123, CLASS_OTHER),
])
def test_classify_exit_reason(reason, expected):
    assert classify_exit_reason(reason) == expected


def _exit(reason, net, gross=None, costs=None, mint="M"):
    rec = {"event": "exit", "mint": mint, "reason": reason, "net_usd": net}
    if gross is not None:
        rec["gross_usd"] = gross
    if costs is not None:
        rec["costs_usd"] = costs
    return rec


def test_attribution_matches_window3_shape():
    """Window-3's actual shape: rugs pay for the whole window, pivots make
    money. Pins the decision number the confirmation stage must move."""
    exits = ([_exit("RUG: LP drained 95% since entry", -15.64 + i * 0.0, -14.6, 1.04,
                    mint=f"R{i}") for i in range(29)]
             + [_exit("pivot: -35% off peak (>25%)", 2.93, 3.97, 1.04,
                      mint=f"P{i}") for i in range(24)])
    attr = build_attribution(exits)
    assert attr["exits"] == 53
    assert attr["priced_exits"] == 53
    assert attr["unpriced_exits"] == 0
    rug = attr["classes"][CLASS_RUG]
    pivot = attr["classes"][CLASS_PIVOT]
    assert rug["exits"] == 29 and pivot["exits"] == 24
    assert rug["net_usd"] == pytest.approx(-453.56, abs=0.01)
    assert pivot["net_usd"] == pytest.approx(70.32, abs=0.01)
    assert rug["avg_net_usd"] == pytest.approx(-15.64, abs=0.01)
    assert pivot["avg_net_usd"] == pytest.approx(2.93, abs=0.01)
    assert rug["losses"] == 29 and rug["wins"] == 0
    assert pivot["wins"] == 24 and pivot["losses"] == 0
    assert rug["worst_net_usd"] == pytest.approx(-15.64)
    assert pivot["best_net_usd"] == pytest.approx(2.93)
    # totals decompose exactly: nothing invented, nothing dropped
    assert attr["net_usd"] == pytest.approx(-383.24, abs=0.01)
    assert sum(b["net_usd"] for b in attr["classes"].values()) == pytest.approx(
        attr["net_usd"], abs=0.01)
    assert sum(b["exits"] for b in attr["classes"].values()) == attr["exits"]
    # rug's share of a negative total is the (negative) majority of the damage
    assert rug["net_share_pct"] > 100.0


def test_attribution_treats_unpriced_net_as_unpriced_not_zero():
    exits = [_exit("pivot: -10% off peak (>5%)", 5.0),
             {"event": "exit", "mint": "X", "reason": "pivot: volume collapsed"},
             {"event": "exit", "mint": "Y", "reason": "pivot: -1%", "net_usd": True},
             {"event": "exit", "mint": "Z", "reason": "pivot: -1%",
              "net_usd": float("nan")},
             {"event": "exit", "mint": "W", "reason": "pivot: -1%",
              "net_usd": float("inf")},
             {"event": "exit", "mint": "V", "reason": "pivot: -1%", "net_usd": "7"}]
    attr = build_attribution(exits)
    pivot = attr["classes"][CLASS_PIVOT]
    assert attr["exits"] == 6
    assert attr["unpriced_exits"] == 5
    assert attr["priced_exits"] == 1
    assert pivot["unpriced"] == 5
    # avg uses only the priced trip (missing data is never break-even)
    assert pivot["avg_net_usd"] == pytest.approx(5.0)
    assert attr["net_usd"] == 5.0
    assert "excluded from avg" in render_attribution(attr)


def test_attribution_share_null_when_total_is_zero():
    attr = build_attribution([_exit("pivot: x", 5.0), _exit("RUG: x", -5.0)])
    assert attr["net_usd"] == 0.0
    for bucket in attr["classes"].values():
        assert bucket["net_share_pct"] is None


def test_attribution_empty_and_render():
    attr = build_attribution([])
    assert attr["exits"] == 0 and attr["classes"] == {}
    assert "no completed round-trips yet" in render_attribution(attr)


def test_report_carries_attribution_in_both_renderers(tmp_path):
    p = tmp_path / "b.jsonl"
    _write(p, [
        {"event": "entry", "mint": "A", "ts": T0},
        {"event": "exit", "mint": "A", "reason": "RUG: LP drained 95%",
         "net_usd": -20.0, "gross_usd": -19.0, "costs_usd": 1.0},
        {"event": "entry", "mint": "B", "ts": T0},
        {"event": "exit", "mint": "B", "reason": "pivot: -35% off peak",
         "net_usd": 6.0, "gross_usd": 7.0, "costs_usd": 1.0},
    ])
    rep = build_g1_report(p, now=T0 + 8 * DAY)
    assert rep["attribution"]["exits"] == 2
    assert rep["attribution"]["net_usd"] == rep["net_usd"]
    text = render_g1_report(rep)
    assert "G1 exit attribution" in text
    assert "rug" in text and "pivot" in text


def test_cli_attribution_only(tmp_path, capsys):
    from scripts.meme_g1_report import main
    p = tmp_path / "b.jsonl"
    _write(p, [
        {"event": "entry", "mint": "A", "ts": T0},
        {"event": "exit", "mint": "A", "reason": "RUG: LP drained 95%",
         "net_usd": -20.0},
        {"event": "entry", "mint": "B", "ts": T0},
        {"event": "exit", "mint": "B", "reason": "pivot: -35% off peak",
         "net_usd": 6.0},
    ])
    assert main(["--blotter", str(p), "--attribution"]) == 0
    out = capsys.readouterr().out
    assert "G1 exit attribution" in out
    assert "G1 progress" not in out  # attribution-only mode
    assert "rug" in out and "-20.0000" in out

    assert main(["--blotter", str(p), "--attribution", "--json"]) == 0
    attr = json.loads(capsys.readouterr().out)
    assert attr["classes"]["rug"]["net_usd"] == -20.0
