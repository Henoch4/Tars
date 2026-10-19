"""Satellite loop tests: screen→enter→exit cycle, persistence, portfolio.
Hermetic — no network, no keys, no real trades."""
import json
import time

import pytest

from src.meme_loop import OpenPosition, SatelliteLoop
from src.meme_satellite import TokenSnapshot


def _snap(mint="MINT1", **kw):
    base = dict(
        mint=mint, top10_hold_pct=20.0, holder_count=25,
        creator_hold_pct=3.0,
        lp_locked=True, mint_authority_renounced=True,
        freeze_authority_renounced=True, age_s=48 * 3600,
        liquidity_usd=100_000.0, buy_tax_pct=1.0, sell_tax_pct=2.0,
        creator_past_rugs=0, graduated=True, mcap_usd=250_000.0,
    )
    base.update(kw)
    return TokenSnapshot(**base)


def _loop(tmp_path, **kw):
    defaults = dict(
        blotter_path=str(tmp_path / "blotter.jsonl"),
        positions_path=str(tmp_path / "positions.json"),
        entry_size_usd=25.0,
        max_session_usd=100.0,  # enough for several entries
    )
    defaults.update(kw)
    return SatelliteLoop(**defaults)


def test_screen_passes_clean_rejects_dirty(tmp_path):
    loop = _loop(tmp_path)
    snaps = [_snap("GOOD"), _snap("BAD", top10_hold_pct=90.0)]
    passed = loop.screen_candidates(snaps)
    assert [s.mint for s in passed] == ["GOOD"]
    # Every screen is recorded
    records = (tmp_path / "blotter.jsonl").read_text().strip().split("\n")
    assert len(records) == 2
    recs = [json.loads(r) for r in records]
    assert recs[0]["passed"] is True
    assert recs[1]["passed"] is False


def test_enter_creates_position_and_tracks_spend(tmp_path):
    # Realistic sizes: the $5 dust floor (fee-drag viability) rejects $2
    # entries, so the session-cap interplay is exercised at $25.
    loop = _loop(tmp_path, entry_size_usd=25.0, max_session_usd=30.0)
    snap = _snap("M1")
    assert loop.try_enter(snap, price_usd=1.0, liquidity_usd=50_000.0,
                          volume_24h_usd=10_000.0) is True
    assert "M1" in loop.open_positions
    assert loop.spent_usd == 25.0
    # Session cap: 25+25 = 50 > 30 → blocked
    assert loop.try_enter(_snap("M2"), 1.0, 50_000.0, 10_000.0) is False
    # Persistence
    loop2 = _loop(tmp_path)
    assert "M1" in loop2.open_positions
    assert loop2.spent_usd == 25.0


def test_enter_sizes_down_on_thin_liquidity(tmp_path):
    loop = _loop(tmp_path, entry_size_usd=25.0, max_session_usd=100.0)
    assert loop.try_enter(_snap("M1"), price_usd=1.0,
                          liquidity_usd=20_000.0,
                          volume_24h_usd=500_000.0) is True
    assert loop.open_positions["M1"].size_usd == 20.0  # 0.1% of pool


def test_enter_records_symbol_when_known(tmp_path):
    loop = _loop(tmp_path)
    assert loop.try_enter(_snap("M1", symbol="PEPE"), 1.0, 50_000.0, 10_000.0) is True
    assert loop.open_positions["M1"].symbol == "PEPE"
    assert loop.try_enter(_snap("M2"), 1.0, 50_000.0, 10_000.0) is True
    assert loop.open_positions["M2"].symbol == ""  # unobserved -> empty
    pf = loop.portfolio()
    assert pf["positions"]["M1"]["symbol"] == "PEPE"
    assert pf["positions"]["M2"]["symbol"] == "M2"  # falls back to mint[:8]


def test_enter_blocks_dust_below_fee_line(tmp_path):
    loop = _loop(tmp_path, entry_size_usd=25.0, max_session_usd=100.0)
    assert loop.try_enter(_snap("M1"), price_usd=1.0,
                          liquidity_usd=4_000.0,
                          volume_24h_usd=500_000.0) is False
    assert "M1" not in loop.open_positions


def test_exit_on_peak_drawdown(tmp_path):
    loop = _loop(tmp_path, entry_size_usd=25.0)
    loop.try_enter(_snap("M1"), price_usd=1.0, liquidity_usd=50_000.0,
                   volume_24h_usd=50_000.0)
    # Price drops 30% off peak
    exits = loop.check_exits({"M1": {"price": 0.65, "volume_24h_usd": 40_000.0}})
    assert len(exits) == 1
    assert "peak" in exits[0]["reason"]
    assert "M1" not in loop.open_positions
    assert exits[0]["net_usd"] < 0  # lost money


def test_exit_on_time_box(tmp_path):
    loop = _loop(tmp_path, entry_size_usd=25.0)
    loop.try_enter(_snap("M1"), price_usd=1.0, liquidity_usd=50_000.0,
                   volume_24h_usd=50_000.0)
    pos = loop.open_positions["M1"]
    pos.entry_ts = time.time() - (7 * 24 * 3600 + 1)  # past 7d box
    exits = loop.check_exits({"M1": {"price": 1.5, "volume_24h_usd": 40_000.0}})
    assert len(exits) == 1
    assert "time box" in exits[0]["reason"]
    assert exits[0]["net_usd"] > 0  # was up 50%


def test_no_exit_when_calm(tmp_path):
    loop = _loop(tmp_path, entry_size_usd=25.0)
    loop.try_enter(_snap("M1"), price_usd=1.0, liquidity_usd=50_000.0,
                   volume_24h_usd=50_000.0)
    exits = loop.check_exits({"M1": {"price": 1.1, "volume_24h_usd": 40_000.0}})
    assert exits == []
    assert "M1" in loop.open_positions


def test_portfolio_snapshot(tmp_path):
    loop = _loop(tmp_path, entry_size_usd=25.0)
    loop.try_enter(_snap("M1"), price_usd=1.0, liquidity_usd=50_000.0,
                   volume_24h_usd=50_000.0)
    p = loop.portfolio()
    assert p["open_positions"] == 1
    assert p["spent_usd"] == 25.0
    assert p["unrealized_usd"] == 0.0  # entry == last
    assert "M1" in p["positions"]


def test_full_cycle_integration(tmp_path):
    loop = _loop(tmp_path, entry_size_usd=25.0)
    # Round 1: screen + enter
    snaps = [_snap("WINNER"), _snap("LOSER", top10_hold_pct=95.0)]
    snaps[0]._price_usd = 1.0
    snaps[0]._volume_24h_usd = 50_000.0
    result = loop.run_cycle(snaps)
    assert result["entered"] == ["WINNER"]
    assert result["exited"] == []
    assert result["passed"] == 1
    # Round 2: price pumps then crashes
    result2 = loop.run_cycle([], {"WINNER": {"price": 1.5, "volume_24h_usd": 30_000.0}})
    assert result2["exited"] == []  # only 33% off peak... wait, peak updated to 1.5
    # Round 3: price crashes 30% off peak
    result3 = loop.run_cycle([], {"WINNER": {"price": 1.0, "volume_24h_usd": 10_000.0}})
    assert result3["exited"] == ["WINNER"]
    assert result3["portfolio"]["realized_net_usd"] != 0


def test_reentry_blocked_then_allowed_after_cooldown(tmp_path):
    """Regression (2026-10-18): the blotter showed one mint entered/exited
    4x in ~3h (3 losses) because try_enter only deduped OPEN positions.
    A recently-exited mint is now blocked until its cooldown closes, then
    allowed again on a fresh pass. A different mint is never affected."""
    loop = _loop(tmp_path, entry_size_usd=25.0)
    assert loop.try_enter(_snap("M1"), 1.0, 50_000.0, 50_000.0) is True
    # Dead-tape exit (price up, volume collapsed) -> not a washout
    exits = loop.check_exits({"M1": {"price": 1.05, "volume_24h_usd": 1_000.0}})
    assert len(exits) == 1 and "M1" not in loop.open_positions
    assert loop.reentry_cooldown["M1"]["cooldown_s"] == loop.reentry_cooldown_s
    # Immediate re-entry: BLOCKED
    assert loop.try_enter(_snap("M1"), 1.05, 50_000.0, 50_000.0) is False
    assert "M1" not in loop.open_positions
    # A different mint is unaffected by M1's cooldown
    assert loop.try_enter(_snap("M2"), 1.0, 50_000.0, 50_000.0) is True
    # Cooldown window elapsed -> M1 allowed again
    loop.reentry_cooldown["M1"]["ts"] -= loop.reentry_cooldown_s + 1
    assert loop.try_enter(_snap("M1"), 1.05, 50_000.0, 50_000.0) is True


def test_blocked_reentry_is_logged(tmp_path):
    loop = _loop(tmp_path, entry_size_usd=25.0)
    loop.try_enter(_snap("M1"), 1.0, 50_000.0, 50_000.0)
    loop.check_exits({"M1": {"price": 1.05, "volume_24h_usd": 1_000.0}})
    loop.try_enter(_snap("M1"), 1.05, 50_000.0, 50_000.0)
    events = [json.loads(r) for r in
              loop.blotter_path.read_text(encoding="utf-8").strip().split("\n")]
    blocked = [e for e in events if e.get("event") == "entry_blocked"]
    assert blocked, "cooldown block must be recorded for later measurement"
    assert blocked[-1]["reason"] == "re-entry cooldown"
    assert blocked[-1]["remaining_s"] > 0


def test_rug_exit_earns_longer_cooldown(tmp_path):
    loop = _loop(tmp_path, entry_size_usd=25.0)
    loop.try_enter(_snap("M1"), 1.0, 50_000.0, 50_000.0)
    # LP drains 80% since entry -> RUG sentinel
    exits = loop.check_exits({"M1": {"price": 0.9, "volume_24h_usd": 40_000.0,
                                     "lp_reserve_usd": 10_000.0}})
    assert len(exits) == 1 and exits[0]["reason"].startswith("RUG")
    assert loop.reentry_cooldown["M1"]["cooldown_s"] == loop.rug_reentry_cooldown_s


def test_washout_pivot_earns_longer_cooldown(tmp_path):
    loop = _loop(tmp_path, entry_size_usd=25.0)
    loop.try_enter(_snap("M1"), 1.0, 50_000.0, 50_000.0)
    # -28% off peak: a pivot (not the 30%/min rug velocity), but a washout
    exits = loop.check_exits({"M1": {"price": 0.72, "volume_24h_usd": 40_000.0}})
    assert len(exits) == 1 and exits[0]["reason"].startswith("pivot")
    assert exits[0]["net_usd"] <= -0.25 * 25.0
    assert loop.reentry_cooldown["M1"]["cooldown_s"] == loop.rug_reentry_cooldown_s


def test_reentry_cooldown_survives_restart(tmp_path):
    loop = _loop(tmp_path, entry_size_usd=25.0)
    loop.try_enter(_snap("M1"), 1.0, 50_000.0, 50_000.0)
    loop.check_exits({"M1": {"price": 1.05, "volume_24h_usd": 1_000.0}})
    rebuild = _loop(tmp_path, entry_size_usd=25.0)
    assert "M1" in rebuild.reentry_cooldown
    assert rebuild.try_enter(_snap("M1"), 1.05, 50_000.0, 50_000.0) is False


def test_halt_blocks_entries_but_exits_run(tmp_path):
    loop = _loop(tmp_path, entry_size_usd=25.0)
    assert loop.halted() is False
    (tmp_path / "HALT").touch()
    assert loop.halted() is True
    snap = _snap("M1")
    snap._price_usd = 1.0
    snap._volume_24h_usd = 50_000.0
    result = loop.run_cycle([snap])
    assert result["halted"] is True
    assert result["screened"] == 0 and result["entered"] == []
    assert "M1" not in loop.open_positions
    # release: entries resume, halt transitions recorded
    (tmp_path / "HALT").unlink()
    result2 = loop.run_cycle([snap])
    assert result2["halted"] is False
    assert result2["entered"] == ["M1"]
    import json as _json
    events = [_json.loads(r)["event"] for r in
              loop.blotter_path.read_text(encoding="utf-8").strip().split("\n")]
    assert "halt_engaged" in events and "halt_released" in events


def test_halt_survives_loop_rebuild(tmp_path):
    (tmp_path / "HALT").touch()
    loop = _loop(tmp_path)
    assert loop.halted() is True
    assert loop.run_cycle([_snap("M1")])["entered"] == []


def test_default_session_fits_several_base_entries(tmp_path):
    """Coherence regression (2026-10-15): base $25 with a $5 session cap
    silently vetoed EVERY entry (0 + 25 > 5). The default session must fit
    several default base entries; the per-day/per-token caps bind above."""
    from src.meme_loop import ENTRY_SIZE_USD, SatelliteLoop
    loop = SatelliteLoop(blotter_path=str(tmp_path / "b.jsonl"),
                         positions_path=str(tmp_path / "p.json"))
    assert loop.max_session_usd >= 4 * ENTRY_SIZE_USD
    snap = _snap("M1")
    snap._price_usd = 1.0
    snap._volume_24h_usd = 500_000.0
    assert loop.try_enter(snap, price_usd=1.0, liquidity_usd=1_000_000.0,
                          volume_24h_usd=500_000.0) is True


def test_wiring_defaults_off_and_inert(tmp_path):
    """The post-G1 layers (breakers, live hooks) must be OFF by default so
    the paper measurement is unchanged."""
    loop = _loop(tmp_path, entry_size_usd=25.0)
    assert loop.breakers is None
    assert loop.on_entry is None and loop.on_exit is None
    assert loop.try_enter(_snap("M1"), 1.0, 50_000.0, 50_000.0) is True


def test_breaker_blocks_entry_after_daily_loss(tmp_path):
    from src.satellite_breakers import BreakerLimits, SatelliteBreakers
    b = SatelliteBreakers(limits=BreakerLimits(max_daily_loss_usd=5.0))
    loop = _loop(tmp_path, entry_size_usd=25.0, breakers=b)
    b.record_result(-6.0, time.time())  # trips the daily cap
    assert loop.try_enter(_snap("M1"), 1.0, 50_000.0, 50_000.0) is False
    recs = [json.loads(r) for r in
            loop.blotter_path.read_text(encoding="utf-8").strip().split("\n")]
    blocked = [e for e in recs if e.get("event") == "entry_blocked"]
    assert blocked and blocked[-1]["reason"].startswith("breaker:")


def test_breaker_state_persists_across_restart(tmp_path):
    from src.satellite_breakers import BreakerLimits, SatelliteBreakers
    b = SatelliteBreakers(limits=BreakerLimits(max_daily_loss_usd=5.0))
    loop = _loop(tmp_path, entry_size_usd=25.0, breakers=b)
    b.record_result(-6.0, time.time())
    loop._save_state()
    b2 = SatelliteBreakers(limits=BreakerLimits(max_daily_loss_usd=5.0))
    rebuild = _loop(tmp_path, entry_size_usd=25.0, breakers=b2)
    assert rebuild.breakers.daily_loss  # counters restored from disk
    assert rebuild.try_enter(_snap("M2"), 1.0, 50_000.0, 50_000.0) is False


def test_entry_and_exit_hooks_fire(tmp_path):
    seen = {"entry": [], "exit": []}
    loop = _loop(tmp_path, entry_size_usd=25.0,
                 on_entry=lambda r: seen["entry"].append(r),
                 on_exit=lambda r: seen["exit"].append(r))
    loop.try_enter(_snap("M1"), 1.0, 50_000.0, 50_000.0)
    assert seen["entry"] and seen["entry"][0]["mint"] == "M1"
    loop.check_exits({"M1": {"price": 0.65, "volume_24h_usd": 40_000.0}})
    assert seen["exit"] and seen["exit"][0]["mint"] == "M1"


def test_failing_hook_does_not_break_loop(tmp_path):
    def boom(_):
        raise RuntimeError("hook down")

    loop = _loop(tmp_path, entry_size_usd=25.0, on_entry=boom, on_exit=boom)
    assert loop.try_enter(_snap("M1"), 1.0, 50_000.0, 50_000.0) is True
    exits = loop.check_exits({"M1": {"price": 0.65, "volume_24h_usd": 40_000.0}})
    assert len(exits) == 1  # loop completed despite the hook raising


def test_halt_alert_dispatched(tmp_path, monkeypatch):
    import src.alerting as alerting
    sent = []
    monkeypatch.setattr(alerting, "send_alert",
                        lambda event, severity, detail: sent.append((event, severity)))
    loop = _loop(tmp_path, entry_size_usd=25.0)
    (tmp_path / "HALT").touch()
    loop.run_cycle([_snap("M1")])
    assert any(e[0] == "SATELLITE_HALT_ENGAGED" for e in sent)
