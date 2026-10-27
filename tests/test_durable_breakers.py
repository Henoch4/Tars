"""Durable breakers: the auto-tripped halt must outlive the process.

The manual kill switch is already durable (HALT file, meme_loop). The gap
this suite pins is the AUTO layer: daily loss / streak pause / drawdown
halt used to live in memory only, so a restart invented a fresh day and
lifted a trip. These tests pin the durable store (atomic write, fail-closed
load), the restorer (unreadable = halted until manual reset; missing =
first boot; corrupt fields = fresh defaults), the runner wiring (default
OFF is byte-identical paper behavior), and loop-level equivalence for a
never-tripping breaker vs `breakers=None`.
"""
import json
import os
import time

import pytest

from src.breakers_store import read_breaker_state, write_breaker_state
from src.satellite_breakers import BreakerLimits, SatelliteBreakers
from scripts import meme_runner

T = 1_700_000_000.0
DAY = 86400.0


# -- the pure class: apply_state / from_state round-trip -------------------

def test_state_roundtrip_preserves_counters_for_audit():
    b = SatelliteBreakers(limits=BreakerLimits(max_daily_loss_usd=50.0,
                                               drawdown_loss_mult=10.0))
    b.record_result(-30.0, T)
    b.record_result(-25.0, T)          # 55 >= 50: paused next-day, not halted
    b2 = SatelliteBreakers.from_state(b.state())
    assert b2.daily_loss == b.daily_loss
    assert b2.consecutive_losses == b.consecutive_losses
    assert b2.paused_until == b.paused_until
    assert b2.halted is False
    assert b2.new_entries_allowed(0, 0.0, T + 1)[0] is False  # still paused


def test_apply_state_degrades_garbage_to_fresh_defaults():
    b = SatelliteBreakers()
    b.apply_state({
        "daily_loss": {"oops": "x"},   # un-intable key -> whole map drops
        "consecutive_losses": "plenty",
        "paused_until": "tomorrow",
        "halted": "YES",               # truthy survives as True (bool)
        "halt_reason": 42,
    })
    assert b.daily_loss == {} and b.consecutive_losses == 0
    assert b.paused_until == 0.0
    assert b.halted is True and b.halt_reason == "42"


def test_apply_state_ignores_non_dict():
    b = SatelliteBreakers(halted=True, halt_reason="x")
    b.apply_state("not a dict")
    assert b.halted is True  # untouched


# -- the durable store ------------------------------------------------------

def test_write_then_read_roundtrip(tmp_path):
    path = str(tmp_path / "breakers.json")
    b = SatelliteBreakers(limits=BreakerLimits(max_daily_loss_usd=50.0,
                                               drawdown_loss_mult=2.0))
    b.record_result(-105.0, T)         # hard halt
    write_breaker_state(path, b, T)
    assert not (tmp_path / "breakers.json.tmp").exists()  # atomic: no leftover
    state, err = read_breaker_state(path)
    assert err is None
    assert state["ts"] == pytest.approx(T)
    assert state["halted"] is True
    assert "daily loss $105.00" in state["halt_reason"]


def test_read_missing_file_is_first_boot_not_error(tmp_path):
    assert read_breaker_state(str(tmp_path / "none.json")) == (None, None)


def test_read_corrupt_file_reports_unreadable(tmp_path):
    path = tmp_path / "breakers.json"
    path.write_text("{not json", encoding="utf-8")
    state, err = read_breaker_state(str(path))
    assert state is None and err and "unreadable" in err


# -- the restorer (fail-closed semantics) ------------------------------------

def test_restore_preserves_halt(tmp_path):
    path = str(tmp_path / "breakers.json")
    src = SatelliteBreakers(limits=BreakerLimits(max_daily_loss_usd=50.0,
                                                 drawdown_loss_mult=2.0))
    src.record_result(-105.0, T)
    write_breaker_state(path, src, T)
    b = meme_runner.DurableBreakers.restored(path, *read_breaker_state(path))
    allowed, reason = b.new_entries_allowed(0, 0.0, T + DAY)  # next day
    assert allowed is False and "halted" in reason


def test_restore_missing_file_is_fresh_first_boot(tmp_path):
    path = str(tmp_path / "breakers.json")
    b = meme_runner.DurableBreakers.restored(path, None, None)
    assert b.halted is False
    assert b.new_entries_allowed(0, 0.0, T)[0] is True
    assert not (tmp_path / "breakers.json").exists()  # nothing invented


def test_restore_unreadable_state_is_fail_closed_halt(tmp_path, capsys):
    path = tmp_path / "breakers.json"
    path.write_text("<html>502</html>", encoding="utf-8")
    b = meme_runner.DurableBreakers.restored(path, None, "unreadable: ValueError")
    assert b.halted is True
    assert "manual reset" in b.halt_reason
    allowed, reason = b.new_entries_allowed(0, 0.0, T)
    assert allowed is False and "halted" in reason
    assert "BREAKERS" in capsys.readouterr().out


def test_operator_reset_clears_the_unreadable_halt_and_resumes(tmp_path, capsys):
    path = tmp_path / "breakers.json"
    path.write_text("garbage", encoding="utf-8")
    b = meme_runner.DurableBreakers.restored(path, None, "unreadable: ValueError")
    assert b.new_entries_allowed(0, 0.0, T)[0] is False
    b.reset()                                          # explicit operator act
    state, err = read_breaker_state(str(path))
    assert err is None and state["halted"] is False     # file rewritten
    assert b.new_entries_allowed(0, 0.0, T)[0] is True


def test_durable_trips_persist_through_record_result(tmp_path):
    path = str(tmp_path / "breakers.json")
    b = meme_runner.DurableBreakers(path,
                                    BreakerLimits(max_daily_loss_usd=50.0,
                                                  drawdown_loss_mult=10.0))
    b.record_result(-30.0, T)
    b2 = meme_runner.DurableBreakers.restored(path, *read_breaker_state(path))
    assert b2.daily_loss == b.daily_loss
    assert b2.paused_until == b.paused_until           # pause survives
    # and a SECOND loss on the restored instance still appends, not overwrites
    b2.record_result(-15.0, T + 60.0)
    assert b2.daily_loss == b.daily_loss | {int(T // DAY): 45.0}


# -- the runner wiring (env-gated, default OFF) -----------------------------

@pytest.fixture
def _path_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TARS_BREAKERS_PATH", str(tmp_path / "breakers.json"))
    monkeypatch.delenv("TARS_BREAKERS", raising=False)
    return str(tmp_path / "breakers.json")


def test_wiring_default_off_bytes_identical(_path_env):
    assert meme_runner._breakers_enabled() is False
    assert meme_runner._breakers_for_loop() is None       # None == today
    assert not os.path.exists(_path_env)                  # nothing invented


def test_wiring_env_on_returns_durable_instance(_path_env, monkeypatch):
    monkeypatch.setenv("TARS_BREAKERS", "1")
    b = meme_runner._breakers_for_loop()
    assert isinstance(b, meme_runner.DurableBreakers)
    assert b.state_path == _path_env
    assert b.halted is False          # fresh boot: no state file yet


def test_wiring_env_on_survives_restart(_path_env, monkeypatch):
    monkeypatch.setenv("TARS_BREAKERS", "1")
    first = meme_runner._breakers_for_loop()
    first.record_result(-105.0, T)    # trip a hard halt mid-process
    # "restart": the next boot's instance restores from the same path
    second = meme_runner._breakers_for_loop()
    assert second is not first
    allowed, reason = second.new_entries_allowed(0, 0.0, T + DAY)
    assert allowed is False and "halted" in reason    # the kill survives


# -- the loop: a never-tripping breaker is behavior-identical to None --------

def _snap(mint, **kw):
    from src.meme_satellite import TokenSnapshot
    base = dict(mint=mint, top10_hold_pct=20.0, holder_count=25,
                creator_hold_pct=3.0, lp_locked=True,
                mint_authority_renounced=True, freeze_authority_renounced=True,
                age_s=48 * 3600, liquidity_usd=100_000.0, buy_tax_pct=1.0,
                sell_tax_pct=2.0, creator_past_rugs=0, graduated=True,
                mcap_usd=250_000.0)
    base.update(kw)
    return TokenSnapshot(**base)


def _loop(tmp_path, breakers=None):
    from src.meme_loop import SatelliteLoop
    return SatelliteLoop(
        blotter_path=str(tmp_path / f"blotter-{breakers is not None}.jsonl"),
        positions_path=str(tmp_path / f"pos-{breakers is not None}.json"),
        entry_size_usd=25.0, max_session_usd=100.0, breakers=breakers,
    )


def test_loop_equivalence_none_vs_never_tripping_breaker(tmp_path):
    """Three wins in a row with a wired, never-tripping DurableBreakers must
    produce the same entry/exit decisions and PnL as the bare paper loop."""
    outs = []
    for br in (None, meme_runner.DurableBreakers(str(tmp_path / "b.json"))):
        loop = _loop(tmp_path, breakers=br)
        for mint in ("A", "B", "C"):
            assert loop.try_enter(_snap(mint), 1.0, 50_000.0, 10_000.0) is True
        upd = {m: {"price": pos.entry_price * 1.05, "volume_24h_usd": 10_000.0}
               for m, pos in loop.open_positions.items()}
        exits = loop.check_exits(upd)
        outs.append((len(exits), loop.realized_net_usd,
                     loop.spent_usd, int(loop.open_positions.__len__())))
    assert outs[0] == outs[1]


def test_loop_with_wired_breaker_blocks_entries_after_trip(tmp_path):
    """post-G1 semantics: enough losses trip the breaker IN the loop, the
    state file records it, and a fresh loop restores the block."""
    br = meme_runner.DurableBreakers(str(tmp_path / "b.json"),
                                     BreakerLimits(max_daily_loss_usd=20.0,
                                                   drawdown_loss_mult=10.0,
                                                   max_consecutive_losses=99,
                                                   streak_cooldown_s=0.0))
    loop = _loop(tmp_path, breakers=br)
    snap = _snap("RUGGED")
    assert loop.try_enter(snap, 1.0, 50_000.0, 10_000.0) is True
    # exit at entering 4% of entry price: full-size loss, trip the daily cap
    loop.check_exits({"RUGGED": {"price": 0.04, "volume_24h_usd": 10_000.0}})
    # -25 net (full size + costs) >= 20 daily cap -> paused
    loop2 = _loop(tmp_path, breakers=meme_runner.DurableBreakers.restored(
        str(tmp_path / "b.json"), *read_breaker_state(str(tmp_path / "b.json"))))
    blocked = loop2.try_enter(_snap("NEWRUG"), 1.0, 50_000.0, 10_000.0)
    assert blocked is False
    recs = [json.loads(r) for r in
            (tmp_path / "blotter-True.jsonl").read_text().splitlines()]
    block_rows = [r for r in recs
                  if r.get("event") == "entry_blocked" and "paused" in str(r.get("reason", ""))]
    assert block_rows, f"no paused-block row recorded: {[r.get('reason') for r in recs]}"
