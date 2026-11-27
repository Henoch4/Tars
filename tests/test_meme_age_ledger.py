import pytest

from src.meme_age_ledger import AgeVariantLedger, slippage_rate


def test_slippage_tiers_include_impact():
    assert slippage_rate(4_000, 25) > 0.08
    assert slippage_rate(200_000, 25) > 0.01


def test_paper_trade_closes_after_costs():
    ledger = AgeVariantLedger()
    ledger.open_trade(variant="baseline", mint="m", entry_ts=1,
                      entry_price=1.0, liquidity_usd=25_000)
    trade = ledger.close_trade(variant="baseline", mint="m", exit_ts=2,
                               exit_price=2.0, liquidity_usd=25_000)
    assert trade.status == "closed"
    assert trade.net_pnl_usd < 25.0
    assert ledger.summary()["closed"] == 1


def test_failed_sell_is_full_loss():
    ledger = AgeVariantLedger()
    ledger.open_trade(variant="variant", mint="m", entry_ts=1,
                      entry_price=1.0, liquidity_usd=25_000)
    trade = ledger.close_trade(variant="variant", mint="m", exit_ts=2,
                               exit_price=1.5, liquidity_usd=1_000,
                               failure_reason="timeout")
    assert trade.status == "failed_exit"
    assert trade.net_pnl_usd == -25.0
    assert ledger.summary()["failed_exits"] == 1


def test_duplicate_and_position_cap_are_enforced():
    ledger = AgeVariantLedger(max_open_positions=1)
    ledger.open_trade(variant="baseline", mint="m", entry_ts=1,
                      entry_price=1.0, liquidity_usd=25_000)
    with pytest.raises(ValueError):
        ledger.open_trade(variant="baseline", mint="m", entry_ts=1,
                          entry_price=1.0, liquidity_usd=25_000)
    with pytest.raises(RuntimeError):
        ledger.open_trade(variant="variant", mint="n", entry_ts=1,
                          entry_price=1.0, liquidity_usd=25_000)
