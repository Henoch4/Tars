from dataclasses import dataclass

from src.gta_reconciliation import reconcile_token_balances


@dataclass
class Position:
    token_amount_raw: int | None


def test_reconciliation_passes_on_exact_chain_match():
    result = reconcile_token_balances({"m1": Position(123)}, lambda _mint: 123)
    assert result.ok is True
    assert result.paused is False


def test_reconciliation_pauses_on_mismatch_or_unavailable_read():
    mismatch = reconcile_token_balances({"m1": Position(123)}, lambda _mint: 122)
    assert mismatch.ok is False and mismatch.paused is True
    unavailable = reconcile_token_balances({"m1": Position(123)}, lambda _mint: None)
    assert unavailable.ok is False and unavailable.paused is True


def test_missing_local_token_amount_fails_closed():
    result = reconcile_token_balances({"m1": Position(None)}, lambda _mint: 123)
    assert result.ok is False
    assert "local token amount missing" in result.reason
