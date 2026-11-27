import pytest

from src.gta_solana import read_token_balance


def _response(rows):
    return {"result": {"value": rows}}


def _row(mint="m1", amount="123", decimals=6):
    return {"account": {"data": {"parsed": {"info": {
        "mint": mint,
        "tokenAmount": {"amount": amount, "decimals": decimals},
    }}}}}


def test_reader_sums_matching_accounts_and_ignores_other_mints():
    result = read_token_balance(
        "m1", "owner", post=lambda *_: _response([_row(amount="100"), _row(amount="23"), _row(mint="other")])
    )
    assert result.amount_raw == 123
    assert result.decimals == 6


def test_reader_returns_zero_for_empty_token_accounts():
    result = read_token_balance("m1", "owner", post=lambda *_: _response([]))
    assert result.amount_raw == 0


def test_reader_fails_closed_on_rpc_error_or_malformed_rows():
    with pytest.raises(RuntimeError, match="Solana RPC error"):
        read_token_balance("m1", "owner", post=lambda *_: {"error": {"code": -1}})
    with pytest.raises(RuntimeError, match="malformed"):
        read_token_balance("m1", "owner", post=lambda *_: _response([{"bad": True}]))
