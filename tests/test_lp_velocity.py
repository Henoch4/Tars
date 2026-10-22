"""Unit tests for the LP-velocity confirmation gate (offline, no network).

Doctrine encoded (mirrors meme_satellite tests): missing data is never a
zero; unarmed gates never reject; the replay must be able to trust the
metric values even when the action is only a note.
"""
from __future__ import annotations

import math

import pytest

from src.meme_lp_velocity import (
    DEFAULT_DROP_PCT_PER_MIN,
    LPSample,
    liquidity_verdict,
    pair_drop_pct_per_min,
)


T0 = 1_000_000.0
MINT = "75zGdpJnXpump"


class TestBasicUnits:
    def test_falling_pair_forms_negative_rate(self):
        samples = [(T0, 27000.0), (T0 + 300.0, 2700.0)]  # -90% over 5 min
        rate = pair_drop_pct_per_min(samples[0][1], samples[1][1],
                                     samples[0][0], samples[1][0], [])
        assert rate == pytest.approx(-18.0, rel=1e-3)

    def test_rising_pair_positive_rate(self):
        rate = pair_drop_pct_per_min(1000.0, 1100.0, T0, T0 + 60.0, [])
        assert rate == pytest.approx(10.0, rel=1e-3)

    def test_too_close_to_read(self):
        notes = []
        rate = pair_drop_pct_per_min(1000.0, 999.0, T0, T0 + 1.0, notes)
        assert rate is None
        assert any("too close" in n for n in notes)

    def test_out_of_order_rejected(self):
        notes = []
        rate = pair_drop_pct_per_min(1000.0, 900.0, T0 + 300.0, T0, notes)
        assert rate is None
        assert any("out of order" in n for n in notes)

    def test_zero_reference_rejected(self):
        notes = []
        rate = pair_drop_pct_per_min(0.0, 900.0, T0, T0 + 60.0, notes)
        assert rate is None
        assert any("zero/negative" in n for n in notes)

    def test_non_finite_rejected(self):
        notes = []
        rate = pair_drop_pct_per_min(1000.0, 1000.0, T0, T0 + 60.0, notes)
        assert rate == 0.0
        assert notes == []


class TestVerdictPass:
    def test_flat_liquidity_passes_unarmed_and_armed(self):
        samples = [(T0, 27000.0), (T0 + 300.0, 26950.0)]
        for armed in (False, True):
            vv = liquidity_verdict(MINT, samples, armed=armed)
            assert vv.action == "pass"
            assert vv.drop_pct_per_min is not None and vv.drop_pct_per_min <= 0

    def test_slow_fall_passes(self):
        # -1%/min: below the 4%/min briefing
        samples = [(T0, 27000.0), (T0 + 60.0, 26730.0)]
        vv = liquidity_verdict(MINT, samples, armed=True)
        assert vv.action == "pass"
        assert any("slow" in n for n in vv.notes)

    def test_rising_liquidity_passes(self):
        samples = [(T0, 20100.0), (T0 + 300.0, 27300.0)]
        vv = liquidity_verdict(MINT, samples, armed=True)
        assert vv.action == "pass"
        assert vv.drop_pct_per_min == pytest.approx(7.164, rel=1e-3)


class TestUnarmedNeverRejects:
    def test_faster_than_threshold_unarmed_is_note(self):
        samples = [(T0, 27500.0), (T0 + 300.0, 2700.0)]  # ~-18%/min
        vv = liquidity_verdict(MINT, samples, armed=False)
        assert vv.action == "note"
        assert vv.reason.startswith("liquidity draining")

    def test_same_data_armed_rejects(self):
        samples = [(T0, 27500.0), (T0 + 300.0, 2700.0)]
        vv = liquidity_verdict(MINT, samples, armed=True)
        assert vv.action == "reject"
        assert "18.0%/min" in vv.reason

    def test_pass_and_field_filled_when_just_under_passing(self):
        # threshold 4%/min; this is 3.99 => pass, not reject
        samples = [(T0, 10000.0), (T0 + 60.0, 9601.0)]
        vv = liquidity_verdict(MINT, samples, armed=True)
        assert vv.action == "pass"


class TestInsufficientData:
    def test_single_sample_never_rejects(self):
        vv = liquidity_verdict(MINT, [(T0, 27000.0)], armed=True)
        assert vv.action == "insufficient"
        assert vv.sample_count == 1

    def test_empty_samples(self):
        vv = liquidity_verdict(MINT, [], armed=True)
        assert vv.action == "insufficient"

    def test_unobserved_samples_dropped(self):
        samples = [(T0, None), (T0 + 300.0, 2700.0)]
        vv = liquidity_verdict(MINT, samples, armed=True)
        assert vv.action == "insufficient"

    def test_bool_is_unobserved_not_zero(self):
        vv = liquidity_verdict(MINT, [(T0, True), (T0 + 300.0, 2700.0)], armed=True)
        assert vv.action == "insufficient"

    def test_nan_is_unobserved(self):
        vv = liquidity_verdict(MINT, [(T0, math.nan), (T0 + 300.0, 2700.0)], armed=True)
        assert vv.action == "insufficient"


class TestPairSpanGuard:
    def test_gap_over_episode_window_pair_skipped(self):
        # 27.5k -> 2.7k over 30 minutes: the typo-rug drain must be missed
        # because the pair exceeds the 15-min episode window
        samples = [(T0, 27500.0), (T0 + 1800.0, 2700.0)]
        vv = liquidity_verdict(MINT, samples, armed=True)
        assert vv.action == "insufficient"
        assert any("episode window" in n for n in vv.notes)

    def test_three_samples_one_bad_pair_one_good(self):
        # healthy draw, gap too big to the killer, then live drain
        samples = [(T0, 27000.0), (T0 + 1200.0, 26900.0), (T0 + 1210.0, 25000.0)]
        vv = liquidity_verdict(MINT, samples, armed=True)
        assert vv.action == "reject"
        assert vv.drop_pct_per_min == pytest.approx(-42.379, rel=1e-3)

    def test_worst_pair_wins(self):
        # mild pair then killer pair: verdict rates the KILLER
        samples = [(T0, 27000.0), (T0 + 300.0, 26800.0), (T0 + 600.0, 4000.0)]
        vv = liquidity_verdict(MINT, samples, armed=True)
        assert vv.action == "reject"


class testDataclassAndMixedSamples:
    def test_dataclass_samples_accepted(self):
        samples = [LPSample(T0, 27000.0), LPSample(T0 + 300.0, 2700.0)]
        vv = liquidity_verdict(MINT, samples, armed=True)
        assert vv.action == "reject"

    def test_objects_with_missing_fields(self):
        class Odd:
            ts = T0
            liquidity_usd = None

        vv = liquidity_verdict(MINT, [Odd(), LPSample(T0 + 300.0, 2700.0)], armed=True)
        assert vv.action == "insufficient"


class TestSanityClamp:
    def test_absurd_rate_is_unmeasurable(self):
        # 1e14 liquidity disappearing in 6s: rate > clamp -> rejected metric
        samples = [(T0, 1e14), (T0 + 6.0, 0.5)]
        vv = liquidity_verdict(MINT, samples, armed=True)
        assert vv.action == "insufficient"


class TestOverallVelocityExposedForReplay:
    def test_overall_field_present_on_note(self):
        samples = [(T0, 27500.0), (T0 + 300.0, 2700.0)]
        vv = liquidity_verdict(MINT, samples, armed=False)
        assert vv.overall_drop_pct_per_min == pytest.approx(-18.036, rel=1e-3)
        assert vv.span_s == pytest.approx(300.0)


class TestReplayFold:
    """Offline replay harness guards: fold + counterfactual accounting."""

    def _rows(self):
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from scripts.replay_lp_velocity import fold_blotter, replay
        T = 1_000_000.0
        rug = [
            {"event": "screen", "mint": "M-rug", "passed": True, "ts": T,
             "reasons": ["pool $27,000: exitable"]},
            {"event": "entry", "mint": "M-rug", "ts": T + 1.0,
             "sized_from_liquidity_usd": 27000.0},
            {"event": "screen", "mint": "M-rug", "passed": False, "ts": T + 300.0,
             "reasons": ["pool $2,700 (< $20,000)"]},
            {"event": "exit", "mint": "M-rug", "ts": T + 301.0,
             "reason": "RUG: LP drained", "net_usd": -25.0},
        ]
        winner = [
            {"event": "screen", "mint": "M-win", "passed": True, "ts": T,
             "reasons": ["pool $20,100: exitable"]},
            {"event": "entry", "mint": "M-win", "ts": T + 1.0,
             "sized_from_liquidity_usd": 20100.0},
            {"event": "screen", "mint": "M-win", "passed": True, "ts": T + 300.0,
             "reasons": ["pool $20,500: exitable"]},
            {"event": "exit", "mint": "M-win", "ts": T + 301.0,
             "reason": "PIVOT: ratchet", "net_usd": 5.0},
        ]
        blind = [
            {"event": "screen", "mint": "M-blind", "passed": True, "ts": T,
             "reasons": ["pool $25,000: exitable"]},
            {"event": "entry", "mint": "M-blind", "ts": T + 1.0,
             "sized_from_liquidity_usd": 25000.0},
            {"event": "exit", "mint": "M-blind", "ts": T + 301.0,
             "reason": "PIVOT: quiet exit", "net_usd": 3.0},
        ]
        return rug + winner + blind, fold_blotter, replay

    def test_replay_accounts_and_failopen(self):
        rows, fold_blotter, replay = self._rows()
        mints = fold_blotter(rows)
        report = replay(mints, thresholds=(4.0,), max_delay_s=915.0)
        assert report["entered"] == 3
        assert report["actual_net_usd"] == pytest.approx(-17.0)
        th = report["thresholds"][0]
        # rug rejected (its -25 kept out), winner kept, blind mint fail-open
        assert th["rug_loss_avoided_usd"] == pytest.approx(25.0)
        assert th["winner_killed_usd"] == pytest.approx(0.0)
        assert th["kept_net_usd"] == pytest.approx(-17.0 + 25.0)
        outcomes = {pm[0]: pm[1] for pm in th["per_mint"]}
        assert outcomes == {"M-rug": "REJECT", "M-win": "kept", "M-blind": "missing"}

    def test_pool_commas_parse(self):
        rows, fold_blotter, replay = self._rows()
        assert fold_blotter(rows)["M-rug"]["samples"][0].liquidity_usd == 27000.0

    def test_entry_liquidity_is_a_sample(self):
        rows, fold_blotter, replay = self._rows()
        mints = fold_blotter(rows)
        liqs = [s.liquidity_usd for s in mints["M-rug"]["samples"]]
        assert 27000.0 in liqs and 2700.0 in liqs
