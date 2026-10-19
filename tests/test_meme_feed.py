"""Feed adapter tests: mint parsing, snapshot mapping, fail-closed reads.
Transport is faked (no network, no key); env resolution is hermetic."""
import base64
import struct

import pytest

from src import meme_feed as feed
from src.meme_feed import FeedError, HeliusFeed, parse_mint_authorities, resolve_helius_rpc


def _mint_b64(mint_disc=0, supply=1_000_000_000_000, decimals=6,
              freeze_disc=0) -> str:
    raw = (struct.pack("<I", mint_disc) + b"\x11" * 32
           + struct.pack("<Q", supply) + bytes([decimals, 1])
           + struct.pack("<I", freeze_disc) + b"\x22" * 32)
    assert len(raw) == 82
    return base64.b64encode(raw).decode()


def _fake_post(routes):
    def post(payload):
        method = payload["method"]
        if method not in routes:
            raise AssertionError(f"unexpected method {method}")
        out = routes[method]
        if isinstance(out, Exception):
            raise out
        return out
    return post


def _routes(mint_b64=None, holders=None):
    return {
        "getAccountInfo": {"result": {"value": (
            {"data": [mint_b64, "base64"]} if mint_b64 else None)}},
        "getTokenLargestAccounts": {"result": {"value": holders or []}},
    }


def _holders(amounts):
    return [{"address": f"addr{i}", "amount": str(a)} for i, a in enumerate(amounts)]


def test_parse_renounced_mint():
    info = parse_mint_authorities(_mint_b64(0, 5_000_000_000_000, 6, 0))
    assert info["mint_authority_renounced"] is True
    assert info["freeze_authority_renounced"] is True
    assert info["supply_raw"] == 5_000_000_000_000
    assert info["decimals"] == 6


def test_parse_retained_authorities():
    info = parse_mint_authorities(_mint_b64(1, 100, 9, 1))
    assert info["mint_authority_renounced"] is False
    assert info["freeze_authority_renounced"] is False


def test_parse_short_buffer_raises():
    with pytest.raises(FeedError):
        parse_mint_authorities(base64.b64encode(b"short").decode())


def test_snapshot_maps_chain_fields():
    amounts = [200_000_000_000, 100_000_000_000] + [10_000_000_000] * 8
    f = HeliusFeed(rpc_url="http://fake",
                   post=_fake_post(_routes(_mint_b64(), _holders(amounts))))
    snap = f.snapshot_for_mint("MINT", {"age_s": 100_000.0, "graduated": True})
    # top10 = (200B + 100B + 8x10B) / 1000B = 38%
    assert snap.top10_hold_pct == pytest.approx(38.0)
    assert snap.mint_authority_renounced is True
    assert snap.age_s == 100_000.0
    assert snap.graduated is True
    # Unsupplied extras stay None -> filter SKIP-defaults them.
    assert snap.liquidity_usd is None
    assert snap.creator_hold_pct is None


def test_snapshot_maps_symbol_display_only():
    amounts = [200_000_000_000] + [10_000_000_000] * 9
    f = HeliusFeed(rpc_url="http://fake",
                   post=_fake_post(_routes(_mint_b64(), _holders(amounts))))
    snap = f.snapshot_for_mint("MINT", {"symbol": "PEPE", "graduated": True})
    assert snap.symbol == "PEPE"
    assert f.snapshot_for_mint("MINT", {"graduated": True}).symbol is None


def test_snapshot_rejects_missing_mint():
    f = HeliusFeed(rpc_url="http://fake", post=_fake_post(_routes()))
    with pytest.raises(FeedError):
        f.snapshot_for_mint("NOPE")


def test_excluded_vaults_leave_share_but_not_count():
    # Pool vault holding 600 of 1000 (60%): excluded from the share
    # (program custody, not organic holders); visible count unchanged
    # (conservative floor semantics). The pool address appears DIRECTLY in
    # the holder list here — the fail-soft direct-match path.
    amounts = [600_000_000_000] + [40_000_000_000] * 9
    addrs = ["POOLVAULT"] + [f"addr{i}" for i in range(9)]
    holders = [{"address": a, "amount": str(n)}
               for a, n in zip(addrs, amounts)]
    routes = _routes(_mint_b64(), holders)
    routes["getTokenAccountsByOwner"] = {"result": {"value": []}}
    f = HeliusFeed(rpc_url="http://fake", post=_fake_post(routes))
    snap = f.snapshot_for_mint("MINT", {"pool_address": "POOLVAULT"})
    assert snap.top10_hold_pct == pytest.approx(36.0)  # 9x40B organic
    assert snap.holder_count == 10
    snap2 = f.snapshot_for_mint("MINT", {})
    assert snap2.top10_hold_pct == pytest.approx(96.0)  # vault included


def test_pool_vault_excluded_by_ownership_pair_not_in_list():
    # Regression for the live concentration stall (probed 2026-10-17 on
    # 5/5 recent holder_concentration rejects: pair-in-list=False, the
    # #1 account is the pool-OWNED vault holding 82-96% of supply; raw
    # top10 92.8-99.7% vs owner-excluded 3.6-12.9%). DexScreener's
    # pairAddress is the pool STATE account and never matches a token
    # account directly — exclusion must resolve pool-owned token accounts.
    amounts = [600_000_000_000] + [40_000_000_000] * 9
    addrs = ["VAULTTOKEN"] + [f"addr{i}" for i in range(9)]
    holders = [{"address": a, "amount": str(n)}
               for a, n in zip(addrs, amounts)]
    routes = _routes(_mint_b64(), holders)
    routes["getTokenAccountsByOwner"] = {"result": {"value": [
        {"pubkey": "VAULTTOKEN"}]}}
    f = HeliusFeed(rpc_url="http://fake", post=_fake_post(routes))
    snap = f.snapshot_for_mint("MINT", {"pool_address": "POOLSTATE"})
    assert snap.top10_hold_pct == pytest.approx(36.0)  # vault removed
    assert snap.holder_count == 10


def test_pool_vault_lookup_failure_keeps_inflated_concentration():
    # Fail-closed direction: ownership lookup dies -> fall back to the
    # direct {pool} match (which never fires on real data) -> concentration
    # stays inflated -> the filter rejects MORE, never less.
    amounts = [600_000_000_000] + [40_000_000_000] * 9
    addrs = ["VAULTTOKEN"] + [f"addr{i}" for i in range(9)]
    holders = [{"address": a, "amount": str(n)}
               for a, n in zip(addrs, amounts)]
    routes = _routes(_mint_b64(), holders)
    routes["getTokenAccountsByOwner"] = FeedError("owner lookup down")
    f = HeliusFeed(rpc_url="http://fake", post=_fake_post(routes))
    snap = f.snapshot_for_mint("MINT", {"pool_address": "POOLSTATE"})
    assert snap.top10_hold_pct == pytest.approx(96.0)  # vault included
    assert snap.holder_count == 10


def test_snapshot_reports_visible_holder_count():
    amounts = [200_000_000_000, 100_000_000_000] + [10_000_000_000] * 8
    f = HeliusFeed(rpc_url="http://fake",
                   post=_fake_post(_routes(_mint_b64(), _holders(amounts))))
    assert f.snapshot_for_mint("MINT").holder_count == 10
    f3 = HeliusFeed(rpc_url="http://fake",
                    post=_fake_post(_routes(_mint_b64(), _holders(amounts[:3]))))
    assert f3.snapshot_for_mint("MINT3").holder_count == 3


def test_snapshot_rejects_zero_supply():
    f = HeliusFeed(rpc_url="http://fake", post=_fake_post(
        _routes(_mint_b64(0, 0, 6), _holders([100]))))
    with pytest.raises(FeedError):
        f.snapshot_for_mint("MINT")


def test_rpc_error_object_raises():
    f = HeliusFeed(rpc_url="http://fake",
                   post=lambda p: {"error": {"code": -32602, "message": "bad"}})
    with pytest.raises(FeedError):
        f.snapshot_for_mint("MINT")


def test_snapshot_many_skips_bad_mint_without_aborting():
    good = _routes(_mint_b64(), _holders([500_000_000_000]))

    def post(payload):
        return good[payload["method"]]

    f = HeliusFeed(rpc_url="http://fake", post=post)
    out = f.snapshot_many(["GOOD", 123])  # 123: invalid mint, skipped
    assert out[0][0] == "ok"
    assert out[1][0] == "skipped"


def test_resolve_prefers_explicit_then_url_then_key(monkeypatch):
    monkeypatch.setenv("HELIUS_RPC_URL", "https://url-from-env")
    monkeypatch.setenv("HELIUS_API_KEY", "key-from-env")
    assert resolve_helius_rpc("https://explicit") == "https://explicit"
    assert resolve_helius_rpc() == "https://url-from-env"
    monkeypatch.delenv("HELIUS_RPC_URL")
    assert resolve_helius_rpc().endswith("key-from-env")


def test_resolve_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("HELIUS_RPC_URL", raising=False)
    monkeypatch.delenv("HELIUS_API_KEY", raising=False)
    with pytest.raises(FeedError):
        resolve_helius_rpc(None)


def _token_account_b64(amount: int) -> str:
    # SPL Token / Token-2022 token account: mint(32) + owner(32) + amount u64.
    raw = b"\x00" * 64 + struct.pack("<Q", amount) + b"\x00" * 8
    return base64.b64encode(raw).decode()


def _owner_accounts(amounts):
    return {"result": {"value": [
        {"account": {"data": [_token_account_b64(a), "base64"]}}
        for a in amounts]}}


def test_creator_hold_pct_computed_onchain_when_absent():
    # RugCheck's domain went NXDOMAIN (2026-10-18): creator share must come
    # from chain truth — the deployer's current balance / supply.
    # Regression owner: the 3,854 missing_data graduates.
    amounts = [200_000_000_000] + [10_000_000_000] * 9
    routes = _routes(_mint_b64(), _holders(amounts))  # supply 1000B
    routes["getTokenAccountsByOwner"] = _owner_accounts([60_000_000_000])
    f = HeliusFeed(rpc_url="http://fake", post=_fake_post(routes))
    snap = f.snapshot_for_mint("MINT", {"creator_address": "DEV"})
    assert snap.creator_address == "DEV"
    assert snap.creator_hold_pct == pytest.approx(6.0)  # 60 / 1000


def test_creator_hold_pct_sums_multiple_accounts():
    amounts = [200_000_000_000] + [10_000_000_000] * 9
    routes = _routes(_mint_b64(), _holders(amounts))
    routes["getTokenAccountsByOwner"] = _owner_accounts(
        [30_000_000_000, 30_000_000_000])
    f = HeliusFeed(rpc_url="http://fake", post=_fake_post(routes))
    snap = f.snapshot_for_mint("MINT", {"creator_address": "DEV"})
    assert snap.creator_hold_pct == pytest.approx(6.0)


def test_creator_share_supplied_value_wins_and_skips_rpc():
    # A supplied value (RugCheck while it lived) must short-circuit the
    # on-chain lookup: no getTokenAccountsByOwner route exists here, so a
    # call would raise AssertionError inside the fake transport.
    amounts = [200_000_000_000] + [10_000_000_000] * 9
    f = HeliusFeed(rpc_url="http://fake",
                   post=_fake_post(_routes(_mint_b64(), _holders(amounts))))
    snap = f.snapshot_for_mint(
        "MINT", {"creator_address": "DEV", "creator_hold_pct": 3.0})
    assert snap.creator_hold_pct == pytest.approx(3.0)


def test_creator_share_zero_when_owner_holds_none():
    # Empty owner account list is a REAL zero (deployer sold), not unknown.
    amounts = [200_000_000_000] + [10_000_000_000] * 9
    routes = _routes(_mint_b64(), _holders(amounts))
    routes["getTokenAccountsByOwner"] = {"result": {"value": []}}
    f = HeliusFeed(rpc_url="http://fake", post=_fake_post(routes))
    snap = f.snapshot_for_mint("MINT", {"creator_address": "DEV"})
    assert snap.creator_hold_pct == pytest.approx(0.0)


def test_creator_share_lookup_failure_stays_unobserved():
    # Transport failure must NOT be recorded as 0.0 (that would pass Gate
    # 3b on a lie). The field stays None -> filter SKIP-defaults to
    # missing_data, the house fail-closed direction.
    amounts = [200_000_000_000] + [10_000_000_000] * 9
    routes = _routes(_mint_b64(), _holders(amounts))
    routes["getTokenAccountsByOwner"] = FeedError("owner lookup down")
    f = HeliusFeed(rpc_url="http://fake", post=_fake_post(routes))
    snap = f.snapshot_for_mint("MINT", {"creator_address": "DEV"})
    assert snap.creator_hold_pct is None


def test_creator_share_skips_malformed_accounts():
    amounts = [200_000_000_000] + [10_000_000_000] * 9
    routes = _routes(_mint_b64(), _holders(amounts))
    routes["getTokenAccountsByOwner"] = {"result": {"value": [
        {"account": {"data": None}},                         # no payload
        {"account": {"data": [None, "base64"]}},             # undecodable
        {"pubkey": "X"},                                      # no account
        {"account": {"data": [_token_account_b64(60_000_000_000), "base64"]}},
    ]}}
    f = HeliusFeed(rpc_url="http://fake", post=_fake_post(routes))
    snap = f.snapshot_for_mint("MINT", {"creator_address": "DEV"})
    assert snap.creator_hold_pct == pytest.approx(6.0)


def test_creator_launches_maps_onto_snapshot():
    amounts = [200_000_000_000] + [10_000_000_000] * 9
    f = HeliusFeed(rpc_url="http://fake",
                   post=_fake_post(_routes(_mint_b64(), _holders(amounts))))
    assert f.snapshot_for_mint("MINT", {"creator_launches": 42}).creator_launches == 42
    assert f.snapshot_for_mint("MINT").creator_launches is None
