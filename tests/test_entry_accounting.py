"""Regression: a float spend residue must never stop the satellite entering.

Window #3 died quietly for ~12.5h: 56 sized-down round-trips left
`spent_usd = -3.552713678800501e-15`, `clamp_size` read any negative spend as
a corrupt ledger and returned 0, and `try_enter` returned False without a
record. 720 screens / 20 passes produced 0 entries and 0 entry_blocked rows,
so the funnel looked like "nothing passed" instead of "the loop is dead".

Every test here fails on the pre-fix code.
"""
import json

import pytest

from src.meme_loop import SatelliteLoop
from src.meme_satellite import (PER_DAY_CAP_USD, SPEND_RESIDUE_EPS_USD,
                                TokenSnapshot, clamp_size)

RESIDUE = -3.552713678800501e-15


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
        max_session_usd=1000.0,
    )
    defaults.update(kw)
    return SatelliteLoop(**defaults)


def _events(tmp_path, name):
    path = tmp_path / "blotter.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().strip().split("\n"):
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("event") == name:
            out.append(rec)
    return out


# -- the clamp itself --------------------------------------------------

def test_clamp_tolerates_float_residue():
    assert clamp_size(25.0, RESIDUE) == 25.0
    assert clamp_size(25.0, -SPEND_RESIDUE_EPS_USD / 2) == 25.0


def test_clamp_still_fails_closed_on_real_negative_spend():
    assert clamp_size(25.0, -0.01) == 0.0
    assert clamp_size(25.0, -SPEND_RESIDUE_EPS_USD * 2) == 0.0


def test_clamp_residue_does_not_shrink_room():
    # A dust-negative ledger must not *increase* the day room beyond the cap.
    assert clamp_size(5.0, PER_DAY_CAP_USD - 1.0) == 1.0
    assert clamp_size(10.0, RESIDUE) == 10.0


# -- the loop's accounting --------------------------------------------

def test_single_fractional_closeout_lands_on_exact_zero(tmp_path):
    loop = _loop(tmp_path)
    # 0.1% of $22,844.48 -> $22.84448, a size with no exact binary form.
    assert loop.try_enter(_snap("M1"), 1.0, 22_844.48, 5_000_000.0) is True
    size = loop.open_positions["M1"].size_usd
    assert size == pytest.approx(22.84448)
    loop.check_exits({"M1": {"price": 0.5, "volume_24h_usd": 1.0}})
    assert loop.spent_usd == 0.0
    assert loop.spent_usd >= 0.0


def test_entry_still_works_after_a_closeout(tmp_path):
    """The window-#3 symptom, end to end."""
    loop = _loop(tmp_path)
    assert loop.try_enter(_snap("M1"), 1.0, 22_844.48, 5_000_000.0) is True
    loop.check_exits({"M1": {"price": 0.5, "volume_24h_usd": 1.0}})
    assert loop.spent_usd == 0.0
    assert loop.try_enter(_snap("M2"), 1.0, 22_844.48, 5_000_000.0) is True


def test_many_fractional_closeouts_stay_exact(tmp_path):
    loop = _loop(tmp_path)
    for i in range(20):
        mint = f"M{i}"
        assert loop.try_enter(_snap(mint), 1.0, 22_844.48, 5_000_000.0) is True
        loop.check_exits({mint: {"price": 0.5, "volume_24h_usd": 1.0}})
        assert loop.spent_usd == 0.0
    assert loop.spent_usd == 0.0


# The production residue: four stakes, and position C (opened third) exits
# after D (opened fourth). That close order is what makes the ledger disagree
# with itself by -3.552713678800501e-15 — the exact value in window #3's
# positions.json. A FIFO close-out cancels exactly, which is why the bug takes
# 56 round-trips and a non-monotonic exit order to appear.
WINDOW3_STAKES = [("A", 14_047.59), ("B", 16_195.45),
                  ("C", 23_484.21), ("D", 14_313.0)]


def test_window3_arithmetic_reproduces_a_negative_residue():
    sizes = [liq * 0.001 for _, liq in WINDOW3_STAKES]
    total = 0.0
    for s in sizes:
        total += s
    for i in (0, 1, 3, 2):  # A, B, D, then C
        total -= sizes[i]
    assert total < 0
    assert abs(total) < SPEND_RESIDUE_EPS_USD
    assert clamp_size(25.0, total) > 0  # post-fix: dust is not garbage


def test_window3_exit_order_leaves_no_residue_and_entries_continue(tmp_path):
    loop = _loop(tmp_path)
    for mint, liq in WINDOW3_STAKES:
        assert loop.try_enter(_snap(mint), 1.0, liq, 5_000_000.0) is True
    for mint in ("A", "B", "D"):
        loop.check_exits({mint: {"price": 0.5, "volume_24h_usd": 1.0}})
    loop.check_exits({"C": {"price": 0.5, "volume_24h_usd": 1.0}})
    assert loop.spent_usd == 0.0
    assert loop.try_enter(_snap("E"), 1.0, 20_000.0, 5_000_000.0) is True


def test_repeated_non_fifo_rounds_stay_exact(tmp_path):
    loop = _loop(tmp_path)
    for r in range(10):
        mints = [(f"{r}A", 14_047.59), (f"{r}B", 16_195.45),
                 (f"{r}C", 23_484.21), (f"{r}D", 14_313.0)]
        for mint, liq in mints:
            assert loop.try_enter(_snap(mint), 1.0, liq, 5_000_000.0) is True
        for mint in (mints[0][0], mints[1][0], mints[3][0]):
            loop.check_exits({mint: {"price": 0.5, "volume_24h_usd": 1.0}})
        loop.check_exits({mints[2][0]: {"price": 0.5, "volume_24h_usd": 1.0}})
        assert loop.spent_usd == 0.0
    assert loop.try_enter(_snap("LAST"), 1.0, 20_000.0, 5_000_000.0) is True


def test_persisted_negative_residue_is_sanitized_on_load(tmp_path):
    """The residue survives restarts, so the repair has to sanitize the file
    too — otherwise a deployed fix would still boot into the dead state."""
    (tmp_path / "positions.json").write_text(json.dumps({
        "positions": [], "spent_usd": RESIDUE, "realized_net_usd": -394.79,
        "cycle_count": 1059, "reentry_cooldown": {},
    }))
    loop = _loop(tmp_path)
    assert loop.spent_usd == 0.0
    assert loop.realized_net_usd == pytest.approx(-394.79)
    assert loop.try_enter(_snap("M1"), 1.0, 50_000.0, 50_000.0) is True


def test_persisted_garbage_spend_is_sanitized(tmp_path):
    for bad in ("nope", None, float("nan"), float("inf")):
        (tmp_path / "positions.json").write_text(json.dumps({
            "positions": [], "spent_usd": bad, "realized_net_usd": 0.0,
            "cycle_count": 0, "reentry_cooldown": {},
        }))
        loop = _loop(tmp_path)
        assert loop.spent_usd == 0.0


# -- the previously silent refusals now leave a trace ------------------

def test_zero_clamp_is_recorded(tmp_path):
    loop = _loop(tmp_path)
    loop.spent_usd = PER_DAY_CAP_USD  # day cap exhausted -> room 0
    assert loop.try_enter(_snap("M1"), 1.0, 50_000.0, 50_000.0) is False
    blocked = _events(tmp_path, "entry_blocked")[-1]
    assert blocked["reason"] == "size clamp returned 0"
    assert blocked["spent_usd"] == PER_DAY_CAP_USD


def test_unobserved_price_is_recorded(tmp_path):
    loop = _loop(tmp_path)
    assert loop.try_enter(_snap("M1"), 0.0, 50_000.0, 50_000.0) is False
    blocked = _events(tmp_path, "entry_blocked")[-1]
    assert blocked["reason"] == "price/liquidity unobserved"
    assert blocked["price_usd"] == 0.0


def test_unobserved_liquidity_is_recorded(tmp_path):
    loop = _loop(tmp_path)
    assert loop.try_enter(_snap("M1"), 1.0, 0.0, 50_000.0) is False
    blocked = _events(tmp_path, "entry_blocked")[-1]
    assert blocked["reason"] == "price/liquidity unobserved"
    assert blocked["liquidity_usd"] == 0.0
