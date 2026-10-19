"""Satellite v1 tests: scream filter rejects its targets, SKIP defaults on
missing data, exits/sentinel/clamp behave, blotter nets costs honestly."""
import pytest

from src.meme_satellite import (
    MarketUpdate,
    PaperBlotter,
    PaperPosition,
    TokenSnapshot,
    adaptive_entry_size,
    check_rug,
    clamp_size,
    evaluate_exit,
    scream_filter,
    simulate_deterministic,
)


def _clean(**overrides):
    base = dict(
        mint="So11111111111111111111111111111111111111112",
        top10_hold_pct=20.0,
        holder_count=25,
        creator_hold_pct=3.0,
        lp_locked=True,
        mint_authority_renounced=True,
        freeze_authority_renounced=True,
        age_s=48 * 3600,
        liquidity_usd=100_000.0,
        buy_tax_pct=1.0,
        sell_tax_pct=2.0,
        creator_past_rugs=0,
        graduated=True,
        mcap_usd=250_000.0,
    )
    base.update(overrides)
    return TokenSnapshot(**base)


def test_clean_graduated_token_passes():
    res = scream_filter(_clean())
    assert res.passed is True
    assert res.rejected_by is None


@pytest.mark.parametrize("field,value,code", [
    ("top10_hold_pct", 80.0, "holder_concentration"),
    ("creator_hold_pct", 25.0, "insider_heavy"),
    ("liquidity_usd", 5_000.0, "thin_liquidity"),
    ("buy_tax_pct", 12.0, "honeypot_tax"),
    ("sell_tax_pct", 40.0, "honeypot_tax"),
    ("creator_past_rugs", 2, "creator_history"),
    ("graduated", False, "unconfirmed"),
    ("holder_count", 6, "too_few_holders"),
    ("mcap_usd", 5_000.0, "thin_mcap"),
])
def test_each_check_rejects_its_target(field, value, code):
    res = scream_filter(_clean(**{field: value}))
    assert res.passed is False
    assert res.rejected_by == code
    assert res.reasons  # auditable trail per rejection


def test_demoted_checks_note_but_never_gate():
    """2026-10 restructure + 2026-10-17 Q1: LP lock / authorities / age are
    measured non-predictive, and an UNKNOWN creator history is a data gap,
    not evidence of risk — all must appear in reasons but never reject."""
    for field, value, note in [
        ("lp_locked", False, "LP not locked"),
        ("mint_authority_renounced", False, "mint authority retained"),
        ("freeze_authority_renounced", False, "freeze authority retained"),
        ("age_s", 3600.0, "informational"),
        ("creator_past_rugs", None, "unobserved"),
    ]:
        res = scream_filter(_clean(**{field: value}))
        assert res.passed is True, field
        assert any(note in r for r in res.reasons), (field, res.reasons)


@pytest.mark.parametrize("field", [
    "top10_hold_pct", "holder_count", "creator_hold_pct",
    "liquidity_usd", "mcap_usd",
    "buy_tax_pct", "sell_tax_pct",
])
def test_missing_data_is_skip_not_pass(field):
    """SKIP default: unobserved GATE inputs reject, never default-pass.
    (LP/authorities/age, and — since Q1 2026-10-17 — creator_past_rugs,
    are notes, not gates; absent there is fine.)"""
    res = scream_filter(_clean(**{field: None}))
    assert res.passed is False
    assert res.rejected_by == "missing_data"


def test_unknown_creator_history_is_note_not_reject():
    """Regression for the 2,854-row wall. Policy 2026-10-17 (Q1): a KNOWN
    prior rug still hard-rejects; an UNKNOWN creator record must NOT gate.
    (Was: reject missing_data — absence of data is not evidence of risk.)"""
    unknown = scream_filter(_clean(creator_past_rugs=None))
    assert unknown.passed is True
    assert any("unobserved" in n for n in unknown.reasons)
    assert scream_filter(_clean(creator_past_rugs=1)).rejected_by == "creator_history"
    assert scream_filter(_clean(creator_past_rugs=0)).passed is True


def test_organic_score_is_informational_note():
    res = scream_filter(_clean(organic_score=84.0))
    assert res.passed is True
    assert any("organic score" in n for n in res.reasons)


def test_serial_deployer_launch_count_is_note_only():
    """Jupiter dev_mints (creator_launches) is captured and surfaced as a
    note, but must NEVER gate yet: the distribution isn't measured, so
    promoting it would be an unmeasured threshold (one change at a time).
    A clean token with a huge launch count still passes."""
    res = scream_filter(_clean(creator_launches=893))
    assert res.passed is True
    assert any("launch count 893" in n for n in res.reasons)
    assert any("note only" in n for n in res.reasons)
    absent = scream_filter(_clean())  # unobserved -> no note, still passes
    assert absent.passed is True
    assert not any("launch count" in n for n in absent.reasons)


def test_few_holders_rejects_as_inapplicable_not_concentrated():
    """Median-100% finding: top10 of 6 holders is vacuous (~100% by
    construction). Below MIN_HOLDERS the verdict must be too_few_holders,
    never holder_concentration — mislabeling emptiness as insider control
    would poison every downstream diagnosis."""
    res = scream_filter(_clean(holder_count=6, top10_hold_pct=100.0))
    assert res.passed is False
    assert res.rejected_by == "too_few_holders"


def test_holder_floor_boundary():
    assert scream_filter(_clean(holder_count=9)).rejected_by == "too_few_holders"
    assert scream_filter(_clean(holder_count=10)).passed is True


def test_rugged_true_rejects_first():
    """Gate 0: an independent rugged=TRUE verdict rejects before every
    other check — even an otherwise-perfect graduated snapshot."""
    res = scream_filter(_clean(rugcheck_rugged=True))
    assert res.passed is False
    assert res.rejected_by == "rugged_confirmed"


def test_rugged_false_or_missing_does_not_gate():
    assert scream_filter(_clean(rugcheck_rugged=False)).passed is True
    r = scream_filter(_clean(rugcheck_rugged=None))
    assert r.passed is True
    assert any("unobserved" in note for note in r.reasons)


def test_bool_tax_is_missing_data_not_zero():
    res = scream_filter(_clean(buy_tax_pct=True))
    assert res.passed is False


def test_round_trip_measures_when_taxes_absent():
    """2026-10: honest Solana sellability = measured Jupiter round-trip
    at entry size (sell-leg refusal = the trap itself). With no tax
    supplier, an observed round-trip gates on its own."""
    res = scream_filter(_clean(buy_tax_pct=None, sell_tax_pct=None,
                               round_trip_cost_pct=2.5))
    assert res.passed is True
    assert any("sellable" in r for r in res.reasons)


def test_round_trip_high_cost_rejects_as_honeypot():
    res = scream_filter(_clean(buy_tax_pct=None, sell_tax_pct=None,
                               round_trip_cost_pct=40.0))
    assert res.passed is False
    assert res.rejected_by == "honeypot_tax"


def test_round_trip_bool_is_missing_data():
    res = scream_filter(_clean(buy_tax_pct=None, sell_tax_pct=None,
                               round_trip_cost_pct=True))
    assert res.passed is False
    assert res.rejected_by == "missing_data"


def test_observed_taxes_take_precedence_over_round_trip():
    """Observed taxes gate; the round-trip measurement is only the
    fallback when no supplier exists — never a second opinion."""
    res = scream_filter(_clean(round_trip_cost_pct=40.0))
    assert res.passed is True  # taxes 1%/2% observed: rt note ignored
    res2 = scream_filter(_clean(sell_tax_pct=40.0, round_trip_cost_pct=1.0))
    assert res2.passed is False
    assert res2.rejected_by == "honeypot_tax"


def _pos(**overrides):
    base = dict(mint="M", entry_usd=100.0, entry_price=1.0, peak_price=1.2,
                entry_ts=1_000_000.0, entry_window_volume_usd=50_000.0,
                size_usd=100.0)
    base.update(overrides)
    return PaperPosition(**base)


def _upd(**overrides):
    base = dict(price=1.1, volume_24h_usd=40_000.0, ts=1_000_000.0 + 3600,
                minutes_since_last=60.0)
    base.update(overrides)
    return MarketUpdate(**base)


def test_calm_market_holds():
    assert evaluate_exit(_pos(), _upd())[0] == "hold"


def test_rug_on_lp_drain():
    rugged, reason = check_rug(
        _pos(), _upd(lp_reserve_usd=10_000.0, lp_reserve_at_entry_usd=100_000.0))
    assert rugged is True
    assert "LP drained" in reason
    assert evaluate_exit(
        _pos(), _upd(lp_reserve_usd=10_000.0,
                      lp_reserve_at_entry_usd=100_000.0))[0] == "exit"


def test_rug_on_velocity():
    rugged, _ = check_rug(_pos(peak_price=2.0), _upd(price=0.5, minutes_since_last=2.0))
    assert rugged is True  # -75% in 2 min


def test_time_box_expires():
    decision, reason = evaluate_exit(_pos(), _upd(ts=1_000_000.0 + 8 * 86400))
    assert decision == "exit"
    assert "time box" in reason


def test_peak_drawdown_pivots():
    decision, _ = evaluate_exit(_pos(peak_price=2.0), _upd(price=1.4))
    assert decision == "exit"  # -30% off peak (ratchet armed: >10% line)


def test_profit_ratchet_tightens_in_profit():
    # Peak +50% over entry arms the ratchet: a -10.4% giveback pivots,
    # while the base -25% trail would still hold.
    decision, reason = evaluate_exit(_pos(peak_price=1.5), _upd(price=1.345))
    assert decision == "exit"
    assert ">10%" in reason
    assert evaluate_exit(_pos(peak_price=1.5), _upd(price=1.36))[0] == "hold"


def test_base_trail_stands_below_ratchet_trigger():
    # Peak only +20%: ratchet NOT armed, base -25% trail governs.
    assert evaluate_exit(_pos(peak_price=1.2), _upd(price=0.95))[0] == "hold"
    decision, reason = evaluate_exit(_pos(peak_price=1.2), _upd(price=0.895))
    assert decision == "exit"  # -25.4% off peak
    assert ">25%" in reason


def test_dead_tape_pivots():
    decision, _ = evaluate_exit(_pos(), _upd(volume_24h_usd=5_000.0))
    assert decision == "exit"


def test_raised_sell_tax_pivots():
    decision, _ = evaluate_exit(_pos(), _upd(sell_tax_pct_now=50.0))
    assert decision == "exit"


def test_clamp_binds_per_token_and_per_day():
    assert clamp_size(500.0, 0.0) == 100.0  # per-token cap
    assert clamp_size(50.0, 230.0) == 20.0  # day room left
    assert clamp_size(50.0, 250.0) == 0.0  # day exhausted
    assert clamp_size(-10.0, 0.0) == 0.0
    assert clamp_size("100", 0.0) == 0.0  # stringified size: no trade
    assert clamp_size(True, 0.0) == 0.0
    assert clamp_size(float("nan"), 0.0) == 0.0


def test_adaptive_size_scales_with_conditions():
    # deep pool + fat volume: base untouched (hard caps apply later)
    assert adaptive_entry_size(25.0, 1_000_000.0, 500_000.0) == 25.0
    # thin pool binds first: 0.1% of $20k
    assert adaptive_entry_size(25.0, 20_000.0, 500_000.0) == 20.0
    # thin volume binds first: 1% of $1k
    assert adaptive_entry_size(25.0, 1_000_000.0, 1_000.0) == 10.0
    # dust: below the fee-drag line -> skip, not a $2 trade
    assert adaptive_entry_size(25.0, 4_000.0, 500_000.0) == 0.0
    # garbage fail-closed
    assert adaptive_entry_size(25.0, None, 500_000.0) == 0.0
    assert adaptive_entry_size(25.0, 20_000.0, float("nan")) == 0.0
    assert adaptive_entry_size(-5.0, 20_000.0, 500_000.0) == 0.0
    assert adaptive_entry_size(True, 20_000.0, 500_000.0) == 0.0


def test_blotter_nets_costs_and_survives_rug_to_zero():
    blotter = PaperBlotter()
    blotter.record_entry("M", 100.0, 1.0, 0.0)
    rec = blotter.record_exit(_pos(), 0.0, 3600.0, "RUG: LP drained 100%")
    # -100 gross (total loss as base case) minus slippage both sides + gas.
    assert rec["gross_usd"] == -100.0
    assert rec["costs_usd"] == pytest.approx(100.0 * 0.01 * 2 + 0.5)
    assert rec["net_usd"] == pytest.approx(-100.0 - 2.5)
    assert blotter.net_pnl() == rec["net_usd"]


def test_blotter_entry_respects_clamp():
    blotter = PaperBlotter()
    rec = blotter.record_entry("M", 500.0, 1.0, 0.0)
    assert rec["size_usd"] == 100.0
    assert rec["rejected"] is True


def test_deterministic_seed_reproduces():
    assert [simulate_deterministic().random() for _ in range(3)] == \
           [simulate_deterministic(42).random() for _ in range(3)]
