from pathlib import Path

from src.meme_shadow_runner import observe_once
from src.meme_satellite import TokenSnapshot


def make_snapshot():
    return TokenSnapshot(mint="good", symbol="TEST", top10_hold_pct=10.0,
        holder_count=20, creator_hold_pct=1.0, graduated=True,
        creator_past_rugs=0, creator_launch_count=2, buy_tax_pct=0.0,
        sell_tax_pct=0.0, round_trip_cost_pct=5.0, liquidity_usd=25_000.0,
        price_usd=0.0002, mcap_usd=40_000.0)


def test_observer_records_pass_and_rejection(tmp_path: Path):
    out = tmp_path / "shadow.jsonl"
    rows = observe_once(output=out,
        discover_fn=lambda _limit: [{"mint": "good"}, {"mint": "bad"}],
        price_fn=lambda _mints: {"So11111111111111111111111111111111111111112": 100.0},
        screen_fn=lambda mint, _price: make_snapshot() if mint == "good" else None)
    assert rows["candidates"] == 2
    assert rows["observed"] == 1
    assert rows["rejected"] == 1
    assert '"reason": "enrichment_failed"' in out.read_text(encoding="utf-8")


def test_observer_fails_closed_on_price_failure(tmp_path: Path):
    out = tmp_path / "shadow.jsonl"
    row = observe_once(output=out, price_fn=lambda _mints: {})
    assert row["event"] == "shadow_error"
    assert row["stage"] == "sol_price"


def test_observer_streams_the_same_snapshot_to_callback(tmp_path: Path):
    seen = []
    observe_once(output=tmp_path / "shadow.jsonl",
        discover_fn=lambda _limit: [{"mint": "good"}],
        price_fn=lambda _mints: {"So11111111111111111111111111111111111111112": 100.0},
        screen_fn=lambda _mint, _price: make_snapshot(),
        now=lambda: 1234.5,
        on_snapshot=lambda snapshot, ts: seen.append((snapshot.mint, ts)))
    assert seen == [("good", 1234.5)]


def test_main_shadow_unit_is_inert():
    unit = (Path(__file__).resolve().parents[1] / "deploy" / "tars-meme-shadow.service").read_text(encoding="utf-8")
    assert "Environment=DRY_RUN=true" in unit
    assert "Environment=ALLOW_LIVE=false" in unit
    assert "meme_shadow_runner" in unit
    assert "meme_execute.live_buy" not in unit
