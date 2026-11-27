"""Regression tests for scripts/paper_trade_sol.py — the paper-trading harness
for the validated funding_persistence_z edge on SOL (MASTER_BUILD_PLAN §2.2).

The money path is the part that decides whether a paper result is honest:
slippage draws, adverse fill prices, direction-aware funding accrual, and
realized-PnL arithmetic. These tests pin them offline so a paper net-carry
number can always be traced back to provable math.
"""
import json
import random
import statistics

import pytest

from scripts.paper_trade_sol import (
    BREAK_EVEN_FUNDING,
    FEE_FRAC,
    SLIP_FRAC,
    accrue_funding,
    adverse_fill_price,
    btc_regime_snapshot,
    clears_break_even,
    close_position,
    draw_slippage,
    extract_funding_row,
    load_state,
    save_state,
    update_funding_history,
)


# --- slippage draws ---------------------------------------------------------


def test_draw_slippage_always_adverse_and_positive():
    rng = random.Random(7)
    draws = [draw_slippage(rng) for _ in range(10_000)]
    assert all(d >= 0 for d in draws), "slippage must never be favorable-only"


def test_draw_slippage_is_not_clamped():
    """PaperFillSimulator doctrine: a cap makes the breach path dead code.
    The draw must be able to exceed the mean slippage parameter."""
    rng = random.Random(7)
    draws = [draw_slippage(rng) for _ in range(10_000)]
    assert max(draws) > SLIP_FRAC * 2, (
        f"max draw {max(draws):.6f} never exceeded 2x mean {SLIP_FRAC:.6f} "
        f"— slippage looks clamped"
    )


def test_draw_slippage_centers_near_the_cost_assumption():
    rng = random.Random(7)
    draws = [draw_slippage(rng) for _ in range(50_000)]
    mean = statistics.mean(draws)
    # |gauss(0, s/2)| + s/2 has mean s/2*sqrt(2/pi) + s/2 ≈ 0.898*s
    assert 0.5 * SLIP_FRAC < mean < 1.5 * SLIP_FRAC, (
        f"mean slippage {mean:.6f} far from the {SLIP_FRAC:.6f} assumption"
    )


# --- adverse fill prices ----------------------------------------------------


def test_buy_fills_above_reference():
    rng = random.Random(7)
    slip = draw_slippage(rng)
    fill = adverse_fill_price(100.0, "buy", slip)
    assert fill > 100.0
    assert fill == pytest.approx(100.0 * (1 + slip))


def test_sell_fills_below_reference():
    rng = random.Random(7)
    slip = draw_slippage(rng)
    fill = adverse_fill_price(100.0, "sell", slip)
    assert fill < 100.0
    assert fill == pytest.approx(100.0 * (1 - slip))


# --- funding accrual --------------------------------------------------------


def _pos(direction: str, size_usd: float = 10_000.0) -> dict:
    return {
        "asset": "SOL-USDT-SWAP",
        "direction": direction,
        "entry_price": 100.0,
        "ref_price": 100.0,
        "size_usd": size_usd,
        "entry_ts": 0.0,
        "entry_fee_usd": size_usd * FEE_FRAC,
        "entry_slippage_bps": 3.0,
        "funding_accrued_usd": 0.0,
        "signal_meta": {},
    }


def test_short_collects_positive_funding():
    pos = _pos("SHORT")
    got = accrue_funding(pos, 0.0001, 8 * 3600)  # full 8h period at 1bp
    assert got == pytest.approx(1.0)  # 10k * 1bp = $1 collected


def test_long_pays_positive_funding():
    pos = _pos("LONG")
    paid = accrue_funding(pos, 0.0001, 8 * 3600)
    assert paid == pytest.approx(-1.0)


def test_funding_accrual_is_pro_rated_by_interval():
    pos = _pos("SHORT")
    full = accrue_funding(pos, 0.0001, 8 * 3600)
    quarter = accrue_funding(pos, 0.0001, 2 * 3600)  # 300s default interval class
    assert quarter == pytest.approx(full / 4)


def test_funding_accrual_caps_at_one_period_per_cycle():
    """A long gap (e.g. laptop asleep) must not accrue multiple periods in
    one cycle — each cycle observes ONE period's rate."""
    pos = _pos("SHORT")
    got = accrue_funding(pos, 0.0001, 8 * 3600 * 10)  # 10 periods elapsed
    assert got == pytest.approx(1.0)  # capped at a single period


def test_negative_funding_flips_sides():
    short_gets = accrue_funding(_pos("SHORT"), -0.0002, 8 * 3600)
    long_gets = accrue_funding(_pos("LONG"), -0.0002, 8 * 3600)
    assert short_gets == pytest.approx(-2.0)  # shorts PAY negative funding
    assert long_gets == pytest.approx(2.0)


# --- close_position: realized PnL arithmetic --------------------------------


@pytest.fixture()
def isolated_paths(tmp_path, monkeypatch):
    """Redirect the module's absolute data paths into tmp_path. chdir alone
    cannot redirect module-level Path constants — without this fixture,
    money-path tests polluted the real state/ledger (regression)."""
    monkeypatch.setattr("scripts.paper_trade_sol.STATE_PATH",
                        tmp_path / "paper_sol_state.json")
    monkeypatch.setattr("scripts.paper_trade_sol.LEDGER_PATH",
                        tmp_path / "paper_sol_ledger.jsonl")
    return tmp_path


def _state_with_open_position(direction: str) -> dict:
    return {
        "funding_history": [],
        "price_history": [],
        "open_position": _pos(direction),
        "closed_trades": [],
        "net_carry_usd": 0.0,
        "cycle_count": 0,
    }


def test_close_position_long_profit_math(isolated_paths):
    """LONG: entry 100 ref, exit ref 101 (+1%), 10k size, 5bps fees each side,
    funding +$1 collected. The exit fill is ADVERSE (below 101 for a sell),
    so gross must be derived from the recorded fill — net must reconcile:
    net = gross - entry_fee - exit_fee + funding."""
    state = _state_with_open_position("LONG")
    pos = state["open_position"]
    pos["funding_accrued_usd"] = 1.0

    trade = close_position(state, pos, 101.0, 8 * 3600, "opposite_signal",
                           random.Random(7))
    # Adverse exit fill: a LONG closes via SELL, fill must be BELOW ref 101.
    assert trade["exit_price"] < 101.0
    # Gross from the recorded (adverse) fill, not the reference price.
    gross_from_fill = ((trade["exit_price"] - pos["entry_price"])
                       / pos["entry_price"] * pos["size_usd"])
    assert trade["gross_pnl_usd"] == pytest.approx(gross_from_fill, abs=0.01)
    expected_net = (trade["gross_pnl_usd"] - pos["entry_fee_usd"]
                    - trade["exit_fee_usd"] + 1.0)
    assert trade["net_pnl_usd"] == pytest.approx(expected_net, abs=0.01)
    assert trade["direction"] == "LONG"
    assert state["open_position"] is None
    assert len(state["closed_trades"]) == 1
    assert state["net_carry_usd"] == pytest.approx(trade["net_pnl_usd"])


def test_close_position_short_profit_math(isolated_paths):
    """SHORT: entry 100 ref, exit ref 99 (-1% => ~+1% for short), 10k size,
    funding +$2 collected. A SHORT closes via BUY, so the adverse fill is
    ABOVE 99 — gross derives from the recorded fill, net must reconcile."""
    state = _state_with_open_position("SHORT")
    pos = state["open_position"]
    pos["funding_accrued_usd"] = 2.0

    trade = close_position(state, pos, 99.0, 2 * 8 * 3600, "max_hold",
                           random.Random(7))
    # Adverse exit fill: a SHORT closes via BUY, fill must be ABOVE ref 99.
    assert trade["exit_price"] > 99.0
    gross_from_fill = ((pos["entry_price"] - trade["exit_price"])
                       / pos["entry_price"] * pos["size_usd"])
    assert trade["gross_pnl_usd"] == pytest.approx(gross_from_fill, abs=0.01)
    expected_net = (trade["gross_pnl_usd"] - pos["entry_fee_usd"]
                    - trade["exit_fee_usd"] + 2.0)
    assert trade["net_pnl_usd"] == pytest.approx(expected_net, abs=0.01)
    assert trade["reason"] == "max_hold"


def test_close_position_writes_ledger_record(tmp_path, monkeypatch):
    """§2.2: nothing becomes real without the written record."""
    monkeypatch.setattr("scripts.paper_trade_sol.LEDGER_PATH",
                        tmp_path / "paper_sol_ledger.jsonl")
    state = _state_with_open_position("LONG")
    pos = state["open_position"]

    close_position(state, pos, 101.0, 8 * 3600, "opposite_signal",
                   random.Random(7))
    ledger = tmp_path / "paper_sol_ledger.jsonl"
    assert ledger.exists()
    records = [json.loads(l) for l in
               ledger.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["event"] == "close"
    assert records[-1]["reason"] == "opposite_signal"
    assert "net_pnl_usd" in records[-1]


# --- settled-period funding history (methodology fidelity) ------------------


def _md(period_id: str, rate: float = 0.0001, settled: float | None = None):
    return {"funding_rate": rate, "price": 100.0,
            "period_id": period_id, "settled_rate": settled}


def test_funding_history_appends_one_point_per_period():
    """persist_window=5 means five 8h PERIODS in the validated signal.
    Rapid polls of the same period must NOT inflate history."""
    state = {"funding_history": [], "last_period_id": None}
    assert update_funding_history(state, _md("1000")) is True
    assert update_funding_history(state, _md("1000")) is False  # same period
    assert update_funding_history(state, _md("1000")) is False
    assert update_funding_history(state, _md("8000")) is True   # next period
    assert update_funding_history(state, _md("8000")) is False
    assert len(state["funding_history"]) == 2


def test_funding_history_uses_settled_rate_on_period_rollover():
    """When a period settles, the history point is the SETTLED rate of the
    just-finished period, not the next period's running estimate."""
    state = {"funding_history": [], "last_period_id": None}
    # Rollover observed: fundingTime moved to the new period; settled rate
    # of the previous period is 0.0002, current running rate is 0.0001.
    update_funding_history(state, _md("8000", rate=0.0001, settled=0.0002))
    assert state["funding_history"] == [0.0002]


def test_funding_history_falls_back_to_running_rate_without_settled():
    state = {"funding_history": [], "last_period_id": None}
    update_funding_history(state, _md("8000", rate=0.00015, settled=None))
    assert state["funding_history"] == [0.00015]


def test_funding_history_never_appends_without_period_identity():
    """Fail-closed: no period anchor -> no append (no fabricated cadence)."""
    state = {"funding_history": [], "last_period_id": None}
    assert update_funding_history(state, _md(None)) is False
    assert state["funding_history"] == []
    assert state["last_period_id"] is None


def test_extract_funding_row_parses_okx_payload():
    row = extract_funding_row([{
        "fundingRate": "0.0000753",
        "fundingTime": "1793520000000",
        "settFundingRate": "0.0000584",
    }])
    assert row == {"funding_rate": 0.0000753,
                   "period_id": "1793520000000",
                   "settled_rate": 0.0000584}


def test_extract_funding_row_handles_empty_settled_and_bad_payload():
    assert extract_funding_row([{"fundingRate": "0.0001",
                                 "fundingTime": "1",
                                 "settFundingRate": ""}])["settled_rate"] is None
    assert extract_funding_row([{"fundingTime": "1"}]) is None   # no rate
    assert extract_funding_row([]) is None
    assert extract_funding_row("garbage") is None


# --- break-even carry floor --------------------------------------------------


def test_clears_break_even_short_needs_positive_carry():
    """SHORT collects positive funding: must be >= break-even per period."""
    assert clears_break_even(BREAK_EVEN_FUNDING, "SHORT") is True
    assert clears_break_even(0.0001, "SHORT") is False   # 1bp < 3.05bp floor
    assert clears_break_even(-0.0001, "SHORT") is False


def test_clears_break_even_long_needs_negative_carry():
    """LONG pays positive funding, so it only pays when funding < -floor."""
    assert clears_break_even(-BREAK_EVEN_FUNDING, "LONG") is True
    assert clears_break_even(0.0001, "LONG") is False
    assert clears_break_even(-0.0001, "LONG") is False


def test_break_even_floor_matches_cost_model():
    """The floor comes from BOOK_1_COST_MODEL, not a hardcoded number —
    if the cost model changes, the floor must follow automatically."""
    from src.cost_model import BOOK_1_COST_MODEL as CM
    from src.cost_model import break_even_funding_rate as be_rate
    assert BREAK_EVEN_FUNDING == pytest.approx(be_rate(CM))


# --- BTC regime tag (paperz analysis plan §3) -------------------------------


def test_btc_regime_snapshot_math():
    """Tag = last price vs 90d high from candles (OKX = newest first)."""
    row = {"btc_price": 80000.0,
           "candles": [["1793520000000", "85000", "90000", "78000", "82000"],
                       ["1793433600000", "91000", "95000", "89000", "90000"]]}
    tag = btc_regime_snapshot(row)
    assert tag["btc_price_usd"] == 80000.0
    assert tag["btc_high90"] == 95000.0
    assert tag["btc_drawdown_from_90d_high"] == pytest.approx(80000 / 95000 - 1)


def test_btc_regime_snapshot_unwraps_okx_response_envelope():
    """Regression: the raw OKX payload is {'code','data':[rows]} —
    the helper must unwrap the envelope, not iterate its dict keys."""
    row = {"btc_price": 77980.6,
           "candles": {"code": "0",
                       "data": [["1793462400000", "77542.6", "78207",
                                 "77387.1", "77980.6", "2316537.37"],
                                ["1793376000000", "77570", "91000",
                                 "77500", "77700", "2316537.37"]]}}
    tag = btc_regime_snapshot(row)
    assert tag is not None
    assert tag["btc_high90"] == 91000.0
    assert tag["btc_drawdown_from_90d_high"] == pytest.approx(77980.6 / 91000 - 1)


def test_btc_regime_snapshot_never_blocks_on_bad_data():
    """Fails open to None — a metadata gap must never block an entry."""
    assert btc_regime_snapshot({"btc_price": 0, "candles": []}) is None
    assert btc_regime_snapshot({"candles": []}) is None                # no px
    assert btc_regime_snapshot({"btc_price": 90000.0}) is None         # no candles
    assert btc_regime_snapshot({"btc_price": "nine", "candles": []}) is None
    assert btc_regime_snapshot(None) is None


def test_btc_regime_snapshot_zero_high_is_rejected():
    assert btc_regime_snapshot({"btc_price": 90000.0,
                                "candles": [["1", "0", "0", "0", "0"]]}) is None


# --- state persistence round-trip -------------------------------------------


def test_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.paper_trade_sol.STATE_PATH",
                        tmp_path / "paper_sol_state.json")
    state = _state_with_open_position("SHORT")
    state["funding_history"] = [0.0001, 0.0002]
    state["net_carry_usd"] = -12.34

    save_state(state)
    loaded = load_state()
    assert loaded["funding_history"] == [0.0001, 0.0002]
    assert loaded["open_position"]["direction"] == "SHORT"
    assert loaded["net_carry_usd"] == pytest.approx(-12.34)
    assert (tmp_path / "paper_sol_state.json").exists()


def test_tests_never_touch_real_data_dir():
    """Guard against test pollution: the money-path tests must patch the
    module path constants, not rely on chdir (which cannot redirect
    module-level absolute paths). This regression came from a live run
    that 'closed' a fake position a test had written into the real state."""
    import scripts.paper_trade_sol as pts
    from pathlib import Path
    # Both constants must point inside the repo's data dir (real paths),
    # and no test above may have mutated them after import-time.
    assert pts.STATE_PATH == Path(pts.REPO) / "data" / "paper_sol_state.json"
    assert pts.LEDGER_PATH == Path(pts.REPO) / "data" / "paper_sol_ledger.jsonl"
