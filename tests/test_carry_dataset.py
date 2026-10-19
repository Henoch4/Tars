"""4.4 verification: carry-dataset guarantees pinned on synthetic data.

The fetch (70 symbols, Binance) needs network; the BUILD's correctness —
embargo (no future leakage), per-8h normalization, y_win bar — must hold
regardless of input. These tests run build_symbol/normalize/trailing_stats
against generated CSVs via a patched CARRY dir: no network, hermetic.

Gold-standard test: rows built with future data appended must be
bit-identical (features) to rows built without it for shared timestamps.
"""
import csv

import numpy as np
import pytest

import scripts.build_carry_dataset as bcd

H8 = 8 * 3600 * 1000


def _write_funding(path, n, rate_fn, start=1_700_000_000_000, step=H8):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "rate"])
        for i in range(n):
            w.writerow([start + i * step, rate_fn(i)])


def _write_candles(path, t0, t1_ms):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "o", "h", "l", "c", "v"])
        t = t0
        while t <= t1_ms:
            w.writerow([t, 100.0, 100.0, 100.0, 100.0, 10.0])
            t += 3600 * 1000


@pytest.fixture()
def triplet(tmp_path, monkeypatch):
    monkeypatch.setattr(bcd, "CARRY", tmp_path)
    return tmp_path


def _build_case(d, n_fund, rate_fn):
    t0 = 1_700_000_000_000
    _write_funding(d / "TST_funding.csv", n_fund, rate_fn, start=t0)
    t1 = t0 + (n_fund - 1) * H8
    _write_candles(d / "TST_perp_1h.csv", t0, t1)
    _write_candles(d / "TST_spot_1h.csv", t0, t1)
    return bcd.build_symbol("TST", {}, {})


def test_normalize_doubles_4h_cadence():
    ts = np.arange(10, dtype=np.int64) * (4 * 3600 * 1000)
    out = bcd.normalize_to_8h(ts, np.full(10, 0.0001))
    assert np.allclose(out, 0.0002)


def test_trailing_stats_excludes_current():
    x = np.array([0.0] * 10 + [100.0])
    mean, _ = bcd.trailing_stats(x, 5)
    assert mean[10] == 0.0  # spike at i must not leak into its own stats


def test_rows_stable_under_future_append(triplet):
    import numpy as _np
    base = _build_case(triplet, 115, lambda i: 0.0001)
    assert len(base) > 0
    # append 10 future funding periods (+ matching candles): shared
    # timestamps must yield identical FEATURES (labels legitimately extend
    # as more future realizes — compare r[2:-4], not the last 4 label cols).
    _build_case(triplet, 125, lambda i: 0.0001)
    extended = bcd.build_symbol("TST", {}, {})
    by_ts = {r[1]: r[2:-4] for r in extended}
    for r in base:
        assert r[1] in by_ts, f"row ts={r[1]} vanished after append"
        _np.testing.assert_allclose(
            _np.array(r[2:-4], dtype=float),
            _np.array(by_ts[r[1]], dtype=float),
            rtol=0, atol=0, equal_nan=True,
            err_msg=f"feature leak at ts={r[1]}")


def _full_window_rows(rows, n_fund, per_window=21):
    """Rows whose 7d label spans a complete forward window: build rows start
    at warmup index 90, full while i <= n_fund - 1 - per_window."""
    n_full = max(0, n_fund - 90 - per_window)
    return rows[:n_full]


def test_y_win_clears_twice_costs(triplet):
    # 0.001 raw/8h -> y7d = 21 * 0.001 * 1e4 = 210 bps > 2*35: win
    rows = _build_case(triplet, 115, lambda i: 0.001)
    full = _full_window_rows(rows, 115)
    assert len(full) > 0
    for r in full:
        assert r[-2] == 1  # y_win column


def test_y_win_rejects_thin_carry(triplet):
    # 0.0001 raw/8h -> y7d = 21 bps < 70: no win (full-window rows)
    rows = _build_case(triplet, 115, lambda i: 0.0001)
    full = _full_window_rows(rows, 115)
    assert len(full) > 0
    for r in full:
        assert r[-2] == 0


def test_tail_labels_truncated_documents_behavior(triplet):
    """CHARACTERIZATION (open question, not a contract): the build includes
    tail rows whose 7d window runs past the data end with PARTIAL sums
    (e.g. 1 period instead of 21). The skip fires only on zero forward
    data. Changing this alters the training set the validated model used —
    needs the ML owner's call, so this test pins current behavior to make
    any change deliberate."""
    rows = _build_case(triplet, 115, lambda i: 0.001)
    last = rows[-1]
    # last included row: exactly 1 forward period -> y7d = 1 * 0.001 * 1e4
    assert last[-3] == pytest.approx(10.0)
    assert last[-2] == 0  # 10 bps < 70 bar: a would-be win mislabeled
