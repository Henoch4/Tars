from src.meme_age_runner import AgeVariantRunner
from src.meme_satellite import TokenSnapshot


def snapshot(age=3600.0, mint="m"):
    return TokenSnapshot(mint=mint, symbol="T", top10_hold_pct=10.0,
        holder_count=20, creator_hold_pct=1.0, graduated=True,
        creator_past_rugs=0, creator_launch_count=2, buy_tax_pct=0.0,
        sell_tax_pct=0.0, round_trip_cost_pct=5.0, liquidity_usd=25_000,
        price_usd=0.0002, mcap_usd=40_000, age_s=age)


def test_runner_opens_only_baseline_for_young_token():
    runner = AgeVariantRunner()
    result = runner.ingest(snapshot(600), observed_at=1000)
    assert result.opened == ("baseline",)
    assert runner.summary()["baseline"]["open"] == 1
    assert runner.summary()["variant"]["open"] == 0


def test_runner_opens_both_for_mature_token_and_closes_together():
    runner = AgeVariantRunner()
    result = runner.ingest(snapshot(3600), observed_at=1000)
    assert set(result.opened) == {"baseline", "variant"}
    closed = runner.close("m", exit_price=0.0004, liquidity_usd=25_000, exit_ts=1100)
    assert len(closed) == 2
    assert runner.summary()["baseline"]["closed"] == 1
    assert runner.summary()["variant"]["closed"] == 1


def test_runner_uses_same_failure_for_both_books():
    runner = AgeVariantRunner()
    runner.ingest(snapshot(3600), observed_at=1000)
    closed = runner.close("m", exit_price=0.0001, liquidity_usd=25_000,
                          exit_ts=1100, failure_reason="timeout")
    assert {row["status"] for row in closed} == {"failed_exit"}


def test_runner_marks_both_books_with_same_exit_rule():
    runner = AgeVariantRunner()
    runner.ingest(snapshot(3600), observed_at=1000)
    decisions = runner.mark("m", price=0.0001, liquidity_usd=25_000,
                            ts=1100, minutes_since_last=1)
    assert len(decisions) == 2
    assert all(row["status"] == "closed" for row in decisions)
    assert runner.summary()["baseline"]["open"] == 0
    assert runner.summary()["variant"]["open"] == 0
