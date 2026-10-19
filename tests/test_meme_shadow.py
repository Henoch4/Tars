"""Shadow-filter tests — pure replay of blotter records."""
from src.meme_shadow import per_mint_facts, render_sweep, shadow_gate, sweep


def _records():
    return [
        {"event": "screen", "mint": "GOOD", "passed": True,
         "reasons": ["top10 holds 30.0%: dispersed enough",
                     "jupiter organic score 80 (informational)"]},
        {"event": "screen", "mint": "ROT", "passed": True,
         "reasons": ["top10 holds 44.0%: dispersed enough",
                     "jupiter organic score 20 (informational)"]},
        {"event": "screen", "mint": "NONOTE", "passed": True, "reasons": ["all gates passed"]},
        {"event": "screen", "mint": "REJECTED", "passed": False, "rejected_by": "thin_liquidity",
         "reasons": ["pool $1 low"]},
        {"event": "entry", "mint": "GOOD", "size_usd": 25},
        {"event": "entry", "mint": "ROT", "size_usd": 25},
        {"event": "entry", "mint": "NONOTE", "size_usd": 25},
        {"event": "exit", "mint": "GOOD", "net_usd": 40.0},
        {"event": "exit", "mint": "ROT", "net_usd": -18.0},
        {"event": "exit", "mint": "NONOTE", "net_usd": 3.0},
    ]


def test_per_mint_facts_parses_notes():
    facts = per_mint_facts(_records())
    assert facts["GOOD"]["organic_score"] == 80.0
    assert facts["GOOD"]["top10_hold_pct"] == 30.0
    assert facts["ROT"]["organic_score"] == 20.0
    assert facts["NONOTE"]["organic_score"] is None
    assert facts["REJECTED"]["entered"] is False


def test_shadow_gate_keeps_only_above_threshold():
    facts = per_mint_facts(_records())
    # threshold 50: ROT (20) dropped; GOOD (80) + nonote-kept (missing kept)
    rep = shadow_gate(facts, "organic_score", 50.0)
    assert rep["entered"] == 3
    assert rep["kept"] == 2
    assert rep["actual_net_usd"] == 25.0  # 40 - 18 + 3
    assert rep["hypothetical_net_usd"] == 43.0  # 40 + 3
    assert rep["delta_usd"] == 18.0
    assert [s["mint"] for s in rep["skipped"]] == ["ROT"]


def test_shadow_gate_drop_missing_fail_closed():
    facts = per_mint_facts(_records())
    rep = shadow_gate(facts, "organic_score", 0.0, drop_missing=True)
    assert rep["kept"] == 2
    assert {s["mint"] for s in rep["skipped"]} == {"NONOTE"}


def test_sweep_reports_coverage():
    facts = per_mint_facts(_records())
    rep = sweep(facts, "organic_score", [0, 25, 50, 90])
    assert rep["entered"] == 3
    assert rep["entered_with_metric"] == 2
    assert len(rep["results"]) == 4


def test_render_handles_no_coverage():
    facts = per_mint_facts([{"event": "screen", "mint": "X", "passed": True, "reasons": []},
                            {"event": "entry", "mint": "X"},
                            {"event": "exit", "mint": "X", "net_usd": 1.0}])
    rep = sweep(facts, "organic_score", [10, 50])
    out = render_sweep(rep)
    assert "insufficient data" in out
