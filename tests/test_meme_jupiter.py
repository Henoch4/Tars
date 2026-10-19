"""Jupiter round-trip probe: mocked quote math, sell-leg refusal = the
measurement itself (100%), transport/shape failures = None (fail-closed
upstream). Pins the Gate-4 sellability contract the live filter relies
on — no network in these tests."""
import pytest

from src.meme_execute import PRICE_URL, SWAP_BASE
from src.meme_jupiter import (
    SOL_MINT,
    TOKEN_URL,
    round_trip_cost_pct,
    token_info,
)

MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
PRICE_CALL = f"{PRICE_URL}?ids={MINT}"


def _price(decimals=6, usd=0.0001):
    entry = {"decimals": decimals}
    if usd is not None:
        entry["usdPrice"] = usd
    return {MINT: entry}  # flat, as price/v3 actually returns (no wrapper)


def _quote(out_raw):
    return {"outAmount": str(out_raw)}


def _http(price=_price(), buy=None, sell=None, buy_err=False, sell_err=False):
    """Route GETs by URL; buy/sell default to a healthy 3% cost trip."""
    amount_raw = int(25.0 / 0.0001 * 10 ** 6)  # $25 at $0.0001, 6 decimals
    if buy is None:
        buy = _quote(100_000_000_000)  # buy leg: lamports out
    if sell is None:
        sell = _quote(int(amount_raw * 0.97))  # sell leg: 3% round-trip

    def http(method, url):
        if url == PRICE_CALL:
            if isinstance(price, Exception):
                raise price
            return price
        if url.startswith(f"{SWAP_BASE}/order?"):
            if "inputMint=" + MINT in url:
                if buy_err:
                    raise RuntimeError("jupiter http failed: HTTPError")
                return buy
            if sell_err:
                raise RuntimeError("jupiter http failed: HTTPError")
            return sell
        raise AssertionError(f"unexpected url {url}")

    return http


def test_healthy_pool_costs_three_percent():
    assert round_trip_cost_pct(MINT, http=_http()) == pytest.approx(3.0)


def test_sell_leg_refusal_is_unsellable_not_none():
    assert round_trip_cost_pct(MINT, http=_http(sell_err=True)) == 100.0


def test_sell_leg_zero_out_is_unsellable():
    assert round_trip_cost_pct(MINT, http=_http(sell=_quote("0"))) == 100.0


def test_buy_leg_failure_is_unmeasurable():
    assert round_trip_cost_pct(MINT, http=_http(buy_err=True)) is None


def test_price_shape_failures_unmeasurable():
    assert round_trip_cost_pct(MINT, http=_http(price=_price(decimals=None))) is None
    assert round_trip_cost_pct(MINT, http=_http(price=_price(decimals=True))) is None
    assert round_trip_cost_pct(MINT, http=_http(price={"data": {}})) is None


def test_no_jup_price_falls_back_to_dexscreener():
    http = _http(price=_price(usd=None))
    assert round_trip_cost_pct(MINT, price_usd=0.0001, http=http) == pytest.approx(3.0)


def test_no_price_at_all_unmeasurable():
    http = _http(price=_price(usd=None))
    assert round_trip_cost_pct(MINT, http=http) is None
    assert round_trip_cost_pct(MINT, price_usd=None, http=http) is None
    assert round_trip_cost_pct(MINT, price_usd=True, http=http) is None


def test_legacy_data_wrapper_tolerated():
    """A {"data": {...}} wrapper (other tier/version) must parse too —
    the shape that silently None'd every price before 2026-10-17."""
    wrapped = {"data": {MINT: {"decimals": 6, "usdPrice": 0.0001}}}
    assert round_trip_cost_pct(MINT, http=_http(price=wrapped)) == pytest.approx(3.0)


def test_negative_cost_clamps_to_zero():
    amount_raw = int(25.0 / 0.0001 * 10 ** 6)
    http = _http(sell=_quote(int(amount_raw * 1.005)))  # "profit": rounding
    assert round_trip_cost_pct(MINT, http=http) == 0.0


def test_degenerate_inputs_unmeasurable():
    http = _http()
    assert round_trip_cost_pct("", http=http) is None
    assert round_trip_cost_pct(SOL_MINT, http=http) is None
    # fallback price is only consulted when Jupiter's price is absent
    no_jup = _http(price=_price(usd=None))
    assert round_trip_cost_pct(MINT, price_usd=-1.0, http=no_jup) is None


def test_cost_is_clamped_at_unsellable():
    amount_raw = int(25.0 / 0.0001 * 10 ** 6)
    # sell returns more than double the input (shouldn't happen; pin clamp)
    http = _http(sell=_quote(amount_raw * 3))
    assert round_trip_cost_pct(MINT, http=http) == 0.0


def _profile(**over):
    row = {
        "id": MINT,
        "dev": "DevWaLLetXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        "audit": {"devBalancePercentage": 1.5, "devMints": 10,
                  "topHoldersPercentage": 30.0,
                  "mintAuthorityDisabled": True,
                  "freezeAuthorityDisabled": True},
        "organicScore": 84.0,
        "organicScoreLabel": "high",
        "holderCount": 1234,
    }
    row.update(over)
    return [row]


def _tokhttp(payload):
    def http(method, url):
        if url.startswith(f"{TOKEN_URL}?query="):
            if isinstance(payload, Exception):
                raise payload
            return payload
        raise AssertionError(f"unexpected url {url}")
    return http


def test_token_info_maps_fields():
    p = token_info(MINT, http=_tokhttp(_profile()))
    assert p["dev"] == "DevWaLLetXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    assert p["dev_hold_pct"] == 1.5
    assert p["dev_mints"] == 10
    assert p["top_holders_pct"] == 30.0
    assert p["organic_score"] == 84.0
    assert p["organic_label"] == "high"
    assert p["holder_count"] == 1234


def test_token_info_fail_soft_to_empty():
    assert token_info("", http=_tokhttp(_profile())) == {}
    assert token_info(MINT, http=_tokhttp([])) == {}
    assert token_info(MINT, http=_tokhttp(RuntimeError("boom"))) == {}
    assert token_info(MINT, http=_tokhttp({"data": []})) == {}


def test_token_info_missing_or_bad_types_are_none():
    p = token_info(MINT, http=_tokhttp(
        [{"id": MINT, "organicScore": "x", "audit": {"devMints": True}}]))
    assert p["dev_hold_pct"] is None
    assert p["dev_mints"] is None
    assert p["organic_score"] is None
    assert p["holder_count"] is None


def test_token_info_prefers_matching_id():
    rows = [{"id": "OTHER", "audit": {"devBalancePercentage": 99.0}},
            {"id": MINT, "audit": {"devBalancePercentage": 2.0}}]
    assert token_info(MINT, http=_tokhttp(rows))["dev_hold_pct"] == 2.0


def test_token_info_default_transport_accepts_array(monkeypatch):
    """Regression: meme_execute._http asserts an OBJECT body and raises on
    the array that /tokens/v2/search actually returns (VPS 2026-10-17:
    SwapError 'jupiter returned non-object' -> supplier silently empty).
    The default path must use the list-tolerant transport, not _http."""
    import src.meme_jupiter as mj
    monkeypatch.setattr(mj, "_get_json",
                        lambda url, timeout_s=20.0: _profile())
    p = mj.token_info(MINT)
    assert p["dev_hold_pct"] == 1.5
    assert p["organic_score"] == 84.0


def test_token_info_retries_transient_429_then_succeeds():
    """Regression: the keyless /tokens/v2/search 429s after ~5 calls
    (measured live 2026-10-19). A transient 429 on a graduate must not
    cost it its creator address — one retry recovers it."""
    calls = {"n": 0}

    def http(method, url):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("jupiter http failed: HTTPError 429")
        return _profile()

    slept = []
    p = token_info(MINT, http=http, _sleep=slept.append)
    assert p["dev_hold_pct"] == 1.5
    assert calls["n"] == 3
    assert len(slept) == 2  # slept between attempts, not after the last


def test_token_info_gives_up_after_retry_budget():
    def http(method, url):
        raise RuntimeError("HTTPError 429: Too Many Requests")

    slept = []
    assert token_info(MINT, http=http, _sleep=slept.append) == {}
    assert len(slept) == 2  # 3 attempts -> 2 inter-attempt sleeps


def test_token_info_non_429_fails_fast_without_retry():
    calls = {"n": 0}

    def http(method, url):
        calls["n"] += 1
        raise RuntimeError("HTTPError 500")

    slept = []
    assert token_info(MINT, http=http, _sleep=slept.append) == {}
    assert calls["n"] == 1  # non-429 is not retried
    assert slept == []
