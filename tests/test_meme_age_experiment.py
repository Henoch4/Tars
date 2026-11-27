from pathlib import Path

from src.meme_age_experiment import evaluate_pair, record_pair
from src.meme_satellite import TokenSnapshot


def passing_snapshot(age_s=3600.0):
    return TokenSnapshot(mint="mint", symbol="TEST", top10_hold_pct=10.0,
        holder_count=20, creator_hold_pct=1.0, graduated=True,
        creator_past_rugs=0, creator_launch_count=2, buy_tax_pct=0.0,
        sell_tax_pct=0.0, round_trip_cost_pct=5.0, liquidity_usd=25_000.0,
        price_usd=0.0002, mcap_usd=40_000.0, age_s=age_s)


def test_variant_adds_only_age_rule():
    decision = evaluate_pair(passing_snapshot(600.0), observed_at=1000.0)
    assert decision["baseline"]["passed"] is True
    assert decision["variant"]["rejected_by"] == "variant_min_age"


def test_mature_token_passes_both():
    decision = evaluate_pair(passing_snapshot(1800.0), observed_at=1000.0)
    assert decision["baseline"]["passed"] is True
    assert decision["variant"]["passed"] is True


def test_baseline_rejection_is_not_relabelled_as_age():
    snapshot = passing_snapshot(600.0)
    snapshot.top10_hold_pct = 90.0
    decision = evaluate_pair(snapshot, observed_at=1000.0)
    assert decision["baseline"]["rejected_by"] == "holder_concentration"
    assert decision["variant"]["rejected_by"] == "holder_concentration"


def test_missing_age_fails_variant_closed():
    decision = evaluate_pair(passing_snapshot(None), observed_at=1000.0)
    assert decision["baseline"]["passed"] is True
    assert decision["variant"]["rejected_by"] == "age_missing"


def test_record_pair_writes_causal_snapshot(tmp_path: Path):
    output = tmp_path / "decisions.jsonl"
    decision = record_pair(passing_snapshot(3600.0), output=output, observed_at=1234.5)
    assert decision["experiment"] == "age_variant_v1"
    assert decision["snapshot"]["age_s"] == 3600.0
    assert '"observed_at": 1234.5' in output.read_text(encoding="utf-8")
