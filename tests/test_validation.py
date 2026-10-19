"""Unit tests for src/validation.py — strategy validation pipeline.

Zero dependencies on network or the OKX CLI (pure numpy).
Run: pytest tests/test_validation.py -v
"""
import numpy as np

import pytest

from src.validation import (
    sharpe_ratio,
    max_drawdown,
    calmar_ratio,
    cagr,
    walk_forward_windows,
    probability_of_backtest_overfitting,
    evaluate_parameter_grid,
    validation_report,
    isotonic_calibration,
)


def test_sharpe_zero_for_zero_variance():
    returns = np.full(100, 0.001)
    assert sharpe_ratio(returns) == 0.0


def test_sharpe_positive_for_positive_mean_returns():
    rng = np.random.default_rng(0)
    returns = rng.normal(0.0005, 0.01, 1000)
    assert sharpe_ratio(returns) > 0


def test_max_drawdown_is_nonpositive():
    rng = np.random.default_rng(1)
    returns = rng.normal(0.0002, 0.02, 500)
    mdd = max_drawdown(returns)
    assert mdd <= 0


def test_max_drawdown_known_series():
    # equity goes 1 -> 1.1 -> 0.99 -> 1.05 ; drawdown from peak 1.1 to 0.99 = -10%
    returns = np.array([0.10, -0.10, 0.06060606])
    mdd = max_drawdown(returns)
    assert abs(mdd - (-0.10)) < 1e-6


def test_calmar_ratio_relates_cagr_and_drawdown():
    rng = np.random.default_rng(2)
    returns = rng.normal(0.0004, 0.01, 3 * 365)
    c = calmar_ratio(returns)
    expected = cagr(returns) / abs(max_drawdown(returns))
    assert abs(c - expected) < 1e-9


def test_walk_forward_windows_never_overlap_or_look_ahead():
    windows = list(walk_forward_windows(n_obs=1000, n_folds=5, train_frac=0.7))
    assert len(windows) == 5
    for train, test in windows:
        assert train.stop <= test.start  # train never comes after test starts
        assert test.start < test.stop


def test_pbo_with_fewer_than_two_variants_is_zero():
    assert probability_of_backtest_overfitting([1.0], [1.0]) == 0.0
    assert probability_of_backtest_overfitting([], []) == 0.0


def test_pbo_in_black_box_range():
    # Whatever the ranking does, the simplified probability stays a
    # well-formed probability in [0.5, 1.0] even when IS-best lands OOS worst.
    is_sharpes = [1.0, 0.8, 0.6, 0.4, 0.2]
    oos_sharpes = [0.1, 0.3, 0.5, 0.7, 0.9]  # IS-best is now OOS-worst => overfit risk high
    pbo = probability_of_backtest_overfitting(is_sharpes, oos_sharpes)
    assert 0.5 <= pbo <= 1.0


def test_pbo_high_when_best_is_worst_oos():
    # IS-best (idx 0) ranks last out-of-sample: overfitting is near-certain.
    is_sharpes = [1.0, 0.8, 0.6, 0.4, 0.2]
    oos_sharpes = [0.0, 0.3, 0.5, 0.7, 0.9]
    pbo = probability_of_backtest_overfitting(is_sharpes, oos_sharpes)
    assert pbo > 0.5


def test_evaluate_parameter_grid_flags_pbo_past_50_combos():
    rng = np.random.default_rng(3)
    grid_is = {str(i): rng.normal(0.0003, 0.01, 500) for i in range(51)}
    grid_oos = {k: rng.normal(0.0003, 0.01, 500) for k in grid_is}
    result = evaluate_parameter_grid(grid_is, grid_oos)
    assert result["n_combinations_tested"] == 51
    assert result["warning"] is not None
    assert "pbo" in result


def test_validation_report_cleared_only_when_oos_calmar_bar_met():
    # Strong consistent edge -> high OOS Calmar -> clears the bar.
    rng = np.random.default_rng(4)
    edge = 0.0004
    returns = edge + rng.normal(0, 0.005, 600)
    report = validation_report(returns, returns, calmar_bar=1.0)
    assert report["passes_calmar_bar"] is True
    assert report["cleared_for_paper_trading"] is True


def test_validation_report_never_traded_oos_does_not_vacuously_clear():
    # All-zero OOS returns = the strategy never traded out of sample. Zero
    # drawdown makes Calmar hit the 1e9 cap; that must NOT clear the gate.
    # Regression for the vacuous pass found in the 2026-08-22 real-data run
    # (funding contrarian: 0 trades on BTC/ETH/BNB in 2y, still "PASS").
    rng = np.random.default_rng(7)
    returns = rng.normal(0.0, 0.01, 600)
    report = validation_report(returns, np.zeros(600), calmar_bar=1.0)
    assert report["out_of_sample"]["calmar"] >= 1.0  # the vacuous number itself
    assert report["has_oos_evidence"] is False
    assert report["passes_calmar_bar"] is False
    assert report["cleared_for_paper_trading"] is False


def test_validation_report_fails_high_calmar_bar():
    # Noisy zero-edge series cannot clear a demanding bar.
    rng = np.random.default_rng(5)
    returns = rng.normal(0.0, 0.02, 600)
    report = validation_report(returns, returns, calmar_bar=50.0)
    assert report["passes_calmar_bar"] is False
    assert report["cleared_for_paper_trading"] is False


def test_validation_report_pbo_fail_blocks_clearing():
    # Under a clearly-overfit grid the report must NOT clear even if the
    # headline OOS Calmar bar is met.
    rng = np.random.default_rng(6)
    returns = 0.0004 + rng.normal(0, 0.005, 600)
    grid_is = {f"p{i}": returns.copy() for i in range(20)}
    # Every variant measured on the same series: IS-best is arbitrarily
    # ranked, so PBO will sit near the opaque middle — force a grid whose
    # IS-best is deliberately worst OOS.
    grid_is = {f"p{i}": returns.copy() for i in range(5)}
    grid_is["p_best_is"] = returns + 0.001  # highest IS
    grid_oos = {k: returns.copy() for k in grid_is}
    grid_oos["p_best_is"] = returns - 0.001  # but worst OOS
    report = validation_report(returns, returns, grid_is, grid_oos, calmar_bar=1.0)
    assert report["passes_calmar_bar"] is True
    assert report["pbo_analysis"]["pbo_pass"] is False
    assert report["cleared_for_paper_trading"] is False


class TestNextBarFillRule:
    """TODO 2.8: every signal computed on candle N must only act at candle
    N+1. The gate previously credited pos[j] with the (j, j+1] move -- a
    same-bar fill at a close that is only known after bar j closes
    (lookahead). Regression pins the shifted execution series."""

    def _gate(self):
        from scripts.run_validation_gate import (
            SIDE_COST,
            episode_stats,
            executed_positions,
            net_bar_returns,
        )
        return SIDE_COST, episode_stats, executed_positions, net_bar_returns

    def test_decisions_shift_one_bar_forward(self):
        _, _, executed_positions, _ = self._gate()
        pos = np.array([0.0, 1.0, 1.0, -1.0, 0.0])
        exec_pos = executed_positions(pos)
        assert exec_pos[0] == 0.0  # flat before the first decision can act
        assert list(exec_pos[1:]) == list(pos[:-1])

    def test_signal_bar_move_not_captured(self):
        # Constant long from bar 0: the (0, 1] +10% move happens while the
        # signal is still being computed -- the executed series earns nothing
        # on the first interval, only the entry cost.
        SIDE_COST, _, _, net_bar_returns = self._gate()
        close = np.array([100.0, 110.0, 121.0])
        ret = net_bar_returns(np.array([1.0, 1.0, 1.0]), close)
        assert ret[0] == SIDE_COST * -1  # entry cost, zero price capture
        assert abs(ret[1] - (11.0 / 110.0 - SIDE_COST)) < 1e-12

    def test_one_bar_pulse_pays_round_trip_earns_nothing(self):
        # A signal held for a single bar acts after its bar's move is over:
        # zero price capture, still exactly one entry + one exit side.
        SIDE_COST, episode_stats, _, _ = self._gate()
        close = np.array([100.0, 110.0, 121.0])
        stats = episode_stats(np.array([0.0, 1.0, 0.0]), close)
        assert stats["trades"] == 1
        assert abs(stats["net_return"] - (-2 * SIDE_COST)) < 1e-12

    def test_signal_on_final_bar_never_trades(self):
        # The last bar has no next bar to act on: no trade, no costs.
        # The pre-fix code counted this as a 1-trade round trip.
        _, episode_stats, _, net_bar_returns = self._gate()
        close = np.array([100.0, 110.0, 121.0])
        pos = np.array([0.0, 0.0, 1.0])
        assert list(net_bar_returns(pos, close)) == [0.0, 0.0]
        stats = episode_stats(pos, close)
        assert stats["trades"] == 0
        assert stats["net_return"] == 0.0


class TestIsotonicCalibrationGate:
    """TODO 4.7: the decile verdict that would have caught v2 live
    (confidence that does not rank trades). Monotone + bounded error
    pass; flat/inverted curves fail loudly, never silently."""

    def test_calibrated_model_passes(self):
        rng = np.random.default_rng(11)
        q = rng.uniform(-50.0, 50.0, 1000)
        y = q + rng.normal(0.0, 0.5, 1000)  # tracks predictions tightly
        rep = isotonic_calibration(q, y, tau=0.5)
        assert rep["monotone"] is True
        assert rep["ece"] < 1.0  # small vs the 100-wide prediction range
        assert rep["coverage_ok"] is True
        assert abs(rep["coverage"] - 0.5) <= 0.05

    def test_v2_flat_signature_fails_monotone(self):
        # Predictions spread wide while outcomes run opposite: the decile
        # actual means strictly decrease — confidence anti-ranks trades.
        n = 1000
        q = np.arange(n, dtype=float)
        y = (n - q) + np.random.default_rng(12).normal(0.0, 1.0, n)
        rep = isotonic_calibration(q, y)
        assert rep["monotone"] is False
        assert rep["ece"] > 100.0  # gross miscalibration, far above any sane bound

    def test_coverage_miss_flagged(self):
        rng = np.random.default_rng(13)
        q = rng.uniform(-50.0, 50.0, 1000)
        y = q - 10.0  # everything realizes below prediction: coverage 1.0
        rep = isotonic_calibration(q, y, tau=0.2)
        assert rep["coverage"] == 1.0
        assert rep["coverage_ok"] is False

    def test_decile_structure_sound(self):
        rng = np.random.default_rng(14)
        n = 1005  # not divisible by 10: remainder must be dealt, not dropped
        q = rng.uniform(0.0, 10.0, n)
        y = q + rng.normal(0.0, 0.1, n)
        rep = isotonic_calibration(q, y)
        assert len(rep["deciles"]) == 10
        assert sum(d["n"] for d in rep["deciles"]) == n
        assert all(d["n"] > 0 for d in rep["deciles"])
        pred_means = [d["pred_mean"] for d in rep["deciles"]]
        assert all(b >= a for a, b in zip(pred_means, pred_means[1:]))
        assert len(rep["y_iso"]) == n
        # Isotonic guarantee holds in predicted order, not input order:
        # fit_transform returns values aligned to the input rows.
        in_pred_order = rep["y_iso"][np.argsort(q, kind="stable")]
        assert bool((np.diff(in_pred_order) >= 0).all())

    @pytest.mark.parametrize("q,y", [
        (np.arange(10.0), np.arange(9.0)),  # length mismatch
        (np.arange(5.0), np.arange(5.0)),  # too few for deciles
        (np.array([1.0, float("nan")] * 10), np.arange(20.0)),  # NaN poisons bins
        (np.array([1.0, float("inf")] * 10), np.arange(20.0)),  # inf breaks fit
    ])
    def test_bad_inputs_raise_never_default(self, q, y):
        with pytest.raises(ValueError):
            isotonic_calibration(q, y)