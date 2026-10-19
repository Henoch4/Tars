"""Execution tests: faked HTTP + stub signer. No network, no keys."""
import base64

import pytest

from src import meme_execute as ex
from src.meme_execute import (
    ExecutionLedger,
    SwapError,
    execute_signed,
    live_buy,
    live_sell,
    quote_swap,
    round_trip_viable,
    sign_order_tx,
    token_prices,
)


def _http(routes):
    def call(method, url, body=None, timeout_s=20.0):
        for key, out in routes.items():
            if key in url:
                if isinstance(out, Exception):
                    raise out
                return out
        raise AssertionError(f"unexpected call {method} {url}")
    return call


def _order():
    return {"requestId": "req1",
            "transaction": base64.b64encode(b"fake-tx-bytes").decode()}


def test_quote_validates_inputs():
    http = _http({"/order": _order()})
    out = quote_swap("IN", "OUT", 1000, "TAKER", http=http)
    assert out["requestId"] == "req1"
    for bad in (True, "1000", 0, -5, 1.5):
        with pytest.raises(SwapError):
            quote_swap("IN", "OUT", bad, "TAKER", http=http)
    with pytest.raises(SwapError):
        quote_swap("", "OUT", 1000, "TAKER", http=http)
    with pytest.raises(SwapError):
        quote_swap("IN", "OUT", 1000, "TAKER", slippage_bps=-1, http=http)


def test_quote_requires_request_id():
    with pytest.raises(SwapError):
        quote_swap("IN", "OUT", 1000, "TAKER",
                   http=_http({"/order": {"nope": True}}))


def test_token_prices_maps_and_nones():
    http = _http({"/price": {"data": {"A": {"usdPrice": "1.5"},
                                      "B": {"oops": 1}}}})
    assert token_prices(["A", "B", "C"], http=http) == {
        "A": 1.5, "B": None, "C": None}
    assert token_prices([], http=http) == {}


def test_token_prices_flat_v3_shape():
    """Regression 2026-10-17: price/v3 returns a FLAT body ({mint: {...}}),
    not {"data": {...}}. Unwrapping a missing "data" key silently None'd
    every price and blinded the satellite's price enrichment."""
    http = _http({"/price": {"A": {"usdPrice": "1.5"},
                             "B": {"oops": 1}}})
    assert token_prices(["A", "B", "C"], http=http) == {
        "A": 1.5, "B": None, "C": None}


def test_http_error_typed(monkeypatch):
    """The real transport wraps every failure as SwapError; injected
    fakes replace the transport wholesale (and their errors propagate
    raw — the seam contract)."""
    import urllib.request
    from src import meme_execute as ex

    def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(SwapError):
        ex._http("GET", "https://x.test/")


def test_sign_uses_stub_never_real_keys():
    sig = sign_order_tx(_order(), keypair=None,
                        sign_fn=lambda txb: b"\x02" * 64)
    assert sig == base64.b64encode(b"\x02" * 64).decode()


def test_sign_refuses_missing_transaction():
    with pytest.raises(SwapError):
        sign_order_tx({"requestId": "r"}, keypair=None,
                      sign_fn=lambda txb: b"\x00")


def test_sign_refuses_bad_base64():
    with pytest.raises(SwapError):
        sign_order_tx({"transaction": "!!!not-b64!!!", "requestId": "r"},
                      keypair=None, sign_fn=lambda txb: b"\x00")


def test_execute_requires_explicit_success():
    good = _http({"/execute": {"status": "Success", "signature": "sig1"}})
    assert execute_signed("AAA", "req1", http=good)["signature"] == "sig1"
    for bad in ({"status": "Failed"}, {"status": "Success"},
                {"status": "Success", "signature": ""}):
        with pytest.raises(SwapError):
            execute_signed("AAA", "req1", http=_http({"/execute": bad}))
    with pytest.raises(SwapError):
        execute_signed("", "req1", http=good)


def test_hurdle_blocks_cost_dominated_size():
    ok, _ = round_trip_viable(100.0, 5.0)  # 5% < 10%
    assert ok is True
    ok, reason = round_trip_viable(2.0, 1.0)  # 50% costs
    assert ok is False and "exceed" in reason
    for bad in [(True, 1.0), ("2", 1.0), (2.0, float("nan")), (0.0, 0.0)]:
        ok, _ = round_trip_viable(*bad)
        assert ok is False


def test_ledger_orders_gates_and_caps():
    ledger = ExecutionLedger(max_session_usd=5.0)
    allowed, _ = ledger.approve(2.0, 0.1)  # 5% costs, within everything
    assert allowed == 2.0
    allowed, reason = ledger.approve(2.0, 1.0)  # hurdle fails first
    assert allowed == 0.0 and "hurdle" in reason
    ledger.spent_usd = 4.5
    allowed, reason = ledger.approve(2.0, 0.1)
    assert allowed == 0.0 and "session cap" in reason


def test_live_buy_paper_without_confirm():
    ledger = ExecutionLedger()
    http = _http({"/order": _order()})
    out = live_buy("IN", "OUT", 1000, "TAKER", "/nope/key.json",
                   0.05, 1.0, ledger, http=http)
    assert out["broadcast"] is False
    assert "paper quote" in out["reason"]
    assert ledger.fills == []  # nothing recorded


def test_live_buy_blocked_up_front_never_quotes():
    def boom(method, url, body=None, timeout_s=20.0):
        raise AssertionError("quote must not fire when blocked")

    ledger = ExecutionLedger()
    out = live_buy("IN", "OUT", 1000, "TAKER", "/nope/key.json",
                   5.0, 1.0, ledger, http=boom)
    assert out["broadcast"] is False


def test_live_buy_full_path_with_stubs():
    ledger = ExecutionLedger()
    http = _http({"/order": _order(),
                   "/execute": {"status": "Success", "signature": "sig9"}})
    out = live_buy("IN", "OUT", 1000, "TAKER", "/nope/key.json",
                   0.05, 1.0, ledger, confirm="GO $1 test",
                   http=http, sign_fn=lambda txb: b"\x03" * 64)
    # sign_fn stub bypasses load_book2_keypair: no key file touched.
    assert out["broadcast"] is True
    assert out["receipt"]["signature"] == "sig9"
    assert len(ledger.fills) == 1


def test_live_sell_paper_without_confirm():
    ledger = ExecutionLedger()
    http = _http({"/order": _order()})
    out = live_sell("TOKEN", ex.SOL_MINT, 1000, "TAKER", "/nope/key.json",
                    http=http, ledger=ledger)
    assert out["broadcast"] is False
    assert "paper quote" in out["reason"]
    assert ledger.fills == []


def test_live_sell_full_path_with_stubs():
    ledger = ExecutionLedger()
    http = _http({"/order": _order(),
                   "/execute": {"status": "Success", "signature": "sigS"}})
    out = live_sell("TOKEN", ex.SOL_MINT, 1000, "TAKER", "/nope/key.json",
                    confirm="SELL IT", http=http,
                    sign_fn=lambda txb: b"\x04" * 64, ledger=ledger)
    assert out["broadcast"] is True
    assert out["receipt"]["signature"] == "sigS"
    assert len(ledger.fills) == 1


def test_live_sell_bypasses_entry_session_cap():
    """An exit must never be blocked by the BUY-side cap: with the session
    budget fully spent, a sell still broadcasts (it frees capital)."""
    ledger = ExecutionLedger(max_session_usd=1.0)
    ledger.spent_usd = 1.0  # buy path would refuse here
    http = _http({"/order": _order(),
                   "/execute": {"status": "Success", "signature": "sigX"}})
    out = live_sell("TOKEN", ex.SOL_MINT, 1000, "TAKER", "/nope/key.json",
                    confirm="SELL IT", http=http,
                    sign_fn=lambda txb: b"\x05" * 64, ledger=ledger)
    assert out["broadcast"] is True
    assert ledger.spent_usd == 1.0  # unchanged: sells don't consume budget


def test_live_sell_refuses_bad_amount():
    http = _http({"/order": _order()})
    for bad in (0, -1, True, "100"):
        with pytest.raises(SwapError):
            live_sell("TOKEN", ex.SOL_MINT, bad, "TAKER", "/nope/key.json",
                      confirm="GO", http=http)
