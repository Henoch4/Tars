"""Runner regression: _discover-shaped dict candidates must flow through.

Regression for the 2026-10-13 bug where run_one_round double-wrapped the
[{"mint":..., "via":...}] dicts returned by _discover, so every candidate's
mint was a dict, failed the isinstance(str) guard, and all 10 printed
"skip [malformed candidate]" every cycle (screened 0 forever).
"""
from scripts import meme_runner


class _StubLoop:
    def __init__(self):
        self.max_candidates = 10
        self.cycle_count = 0
        self.open_positions = {}
        self.seen = None

    def run_cycle(self, snapshots, price_updates):
        self.seen = snapshots
        return {"screened": len(snapshots), "passed": 0, "entered": [],
                "exited": [], "exit_details": [],
                "portfolio": {"open_positions": 0, "spent_usd": 0.0,
                              "unrealized_usd": 0.0, "realized_net_usd": 0.0}}


def test_run_one_round_accepts_discover_dicts(monkeypatch, capsys):
    monkeypatch.setattr(meme_runner, "_enrich",
                        lambda mint, via=None: (object(), None))
    loop = _StubLoop()
    cands = [{"mint": f"Mint{i:040d}", "via": "graduated"} for i in range(3)]
    result = meme_runner.run_one_round(loop, candidates=cands)
    out = capsys.readouterr().out
    assert "malformed" not in out
    assert result["screened"] == 3
    assert len(loop.seen) == 3


def test_run_one_round_accepts_bare_strings(monkeypatch, capsys):
    monkeypatch.setattr(meme_runner, "_enrich",
                        lambda mint, via=None: (object(), None))
    loop = _StubLoop()
    result = meme_runner.run_one_round(loop, candidates=["MintAAA", "MintBBB"])
    out = capsys.readouterr().out
    assert "malformed" not in out
    assert result["screened"] == 2


def test_run_one_round_still_rejects_garbage(monkeypatch, capsys):
    monkeypatch.setattr(meme_runner, "_enrich",
                        lambda mint, via=None: (object(), None))
    loop = _StubLoop()
    result = meme_runner.run_one_round(
        loop, candidates=[{"mint": None, "via": "x"}, {"no_mint": 1},
                          "GoodMint111"])
    out = capsys.readouterr().out
    assert out.count("malformed") == 2
    assert result["screened"] == 1


def test_diagnose_histograms_rejected_by(tmp_path, monkeypatch, capsys):
    import json
    import src.meme_loop as ml
    blotter = tmp_path / "blotter.jsonl"
    events = [
        {"event": "screen", "mint": "A", "passed": True,
         "rejected_by": None, "reasons": []},
        {"event": "screen", "mint": "B", "passed": False,
         "rejected_by": "missing_data", "reasons": ["x unobserved"]},
        {"event": "screen", "mint": "C", "passed": False,
         "rejected_by": "missing_data", "reasons": ["y unobserved"]},
        {"event": "screen", "mint": "D", "passed": False,
         "rejected_by": "thin_liquidity", "reasons": ["pool small"]},
        {"event": "screen", "mint": "E", "passed": False,
         "rejected_by": "holder_concentration",
         "reasons": ["top10 holds 73.2% (> 50%)"]},
        {"event": "entry", "mint": "A"},
    ]
    blotter.write_text("\n".join(json.dumps(e) for e in events),
                       encoding="utf-8")
    monkeypatch.setattr(ml, "BLOTTER_PATH", str(blotter))
    assert meme_runner.show_diagnose() == 0
    out = capsys.readouterr().out
    assert "5 screened, 1 passed" in out
    assert "missing_data" in out and "thin_liquidity" in out
    assert out.index("missing_data") < out.index("thin_liquidity")
    assert "median 73.2%" in out


def test_forensics_cache_hit_avoids_network(tmp_path, monkeypatch):
    import json
    import src.meme_loop as ml
    monkeypatch.setattr(ml, "BLOTTER_PATH", str(tmp_path / "blotter.jsonl"))
    cached = {"mint": "M1", "block0_buyers": 4, "dev_sold_minute1": False}
    cdir = tmp_path / "forensics"
    cdir.mkdir()
    (cdir / "M1.json").write_text(json.dumps(cached), encoding="utf-8")
    out = meme_runner._cached_forensics("M1", creator="C1")
    assert out["block0_buyers"] == 4


def test_forensics_cache_refetches_for_missing_dev_verdict(tmp_path,
                                                           monkeypatch):
    import json
    import src.meme_loop as ml
    monkeypatch.setattr(ml, "BLOTTER_PATH", str(tmp_path / "blotter.jsonl"))
    cdir = tmp_path / "forensics"
    cdir.mkdir()
    (cdir / "M1.json").write_text(json.dumps(
        {"mint": "M1", "block0_buyers": 4, "dev_sold_minute1": None}),
        encoding="utf-8")
    calls = []

    class FakeLF:
        def examine(self, mint, creator=None):
            calls.append((mint, creator))
            return {"mint": mint, "block0_buyers": 4,
                    "dev_sold_minute1": True}

    monkeypatch.setattr("src.meme_launch.LaunchForensics", FakeLF)
    out = meme_runner._cached_forensics("M1", creator="C1")
    assert calls == [("M1", "C1")]
    assert out["dev_sold_minute1"] is True


def test_diagnose_breaks_down_missing_fields(tmp_path, monkeypatch, capsys):
    import json
    import src.meme_loop as ml
    blotter = tmp_path / "blotter.jsonl"
    events = [
        {"event": "screen", "mint": "A", "passed": False,
         "rejected_by": "missing_data",
         "reasons": ["creator_past_rugs unobserved (type NoneType)"]},
        {"event": "screen", "mint": "B", "passed": False,
         "rejected_by": "missing_data",
         "reasons": ["creator_past_rugs unobserved (type NoneType)"]},
        {"event": "screen", "mint": "C", "passed": False,
         "rejected_by": "missing_data",
         "reasons": ["liquidity_usd unobserved (type NoneType)"]},
    ]
    blotter.write_text("\n".join(json.dumps(e) for e in events),
                       encoding="utf-8")
    monkeypatch.setattr(ml, "BLOTTER_PATH", str(blotter))
    assert meme_runner.show_diagnose() == 0
    out = capsys.readouterr().out
    assert "missing_data by field" in out
    assert out.index("creator_past_rugs") < out.index("liquidity_usd")


def test_diagnose_attributes_fatal_note_not_first(tmp_path, monkeypatch,
                                                  capsys):
    # Gate 0's rugcheck note precedes everything: a row dying later at
    # creator history must attribute to creator_past_rugs, not verdict.
    import json
    import src.meme_loop as ml
    blotter = tmp_path / "blotter.jsonl"
    blotter.write_text("\n".join(json.dumps(e) for e in [
        {"event": "screen", "mint": "A", "passed": False,
         "rejected_by": "missing_data",
         "reasons": ["rugcheck verdict unobserved",
                     "25 holders visible",
                     "creator_past_rugs unobserved (type NoneType)"]},
    ]), encoding="utf-8")
    monkeypatch.setattr(ml, "BLOTTER_PATH", str(blotter))
    assert meme_runner.show_diagnose() == 0
    out = capsys.readouterr().out
    assert "creator_past_rugs" in out
    assert "verdict" not in out.split("missing_data by field")[1]


def test_diagnose_missing_blotter(tmp_path, monkeypatch, capsys):
    import src.meme_loop as ml
    monkeypatch.setattr(ml, "BLOTTER_PATH",
                        str(tmp_path / "absent.jsonl"))
    assert meme_runner.show_diagnose() == 0
    assert "no blotter yet" in capsys.readouterr().out


def test_signals_line_contrasts_via_vs_flag(monkeypatch, capsys):
    from src.meme_satellite import TokenSnapshot

    def snap(mint, graduated):
        return TokenSnapshot(mint=mint, top10_hold_pct=20.0,
                             holder_count=25, graduated=graduated)

    enrich_map = {"G1": (snap("G1", True), None),
                  "G2": (snap("G2", False), None),
                  "N1": (snap("N1", False), None)}
    monkeypatch.setattr(meme_runner, "_enrich",
                        lambda mint, via=None: enrich_map[mint])
    loop = _StubLoop()
    cands = [{"mint": "G1", "via": "graduated"},
             {"mint": "G2", "via": "graduated"},
             {"mint": "N1", "via": "token_create"}]
    meme_runner.run_one_round(loop, candidates=cands)
    out = capsys.readouterr().out
    assert "via[graduated=2,token_create=1]" in out
    # G1: via graduated + flag true; G2: via graduated but flag false —
    # the leak this instrumentation exists to catch.
    assert "graduated:1=1" in out and "graduated:0=1" in out
    assert "token_create:0=1" in out
