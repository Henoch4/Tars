"""Discovery + enrichment tests (faked HTTP, no network, no keys)."""
import pytest

from src import meme_discovery as disc
from src.meme_discovery import (
    DiscoveryError,
    discover_candidates,
    enrich_mint,
    fetch_fresh_mints,
)


def _profiles():
    return [
        {"chainId": "solana", "tokenAddress": "AAAApump"},
        {"chainId": "ethereum", "tokenAddress": "0xabc"},
        {"chainId": "solana", "tokenAddress": "BBB111"},
        {"chainId": "solana", "tokenAddress": "AAAApump"},  # dupe
        {"chainId": "solana", "tokenAddress": ""},  # empty
        "garbage",
    ]


def _pair(dex="raydium", liq=50_000.0, created=1_700_000_000_000,
          vol=10_000.0, price="0.5", buys=100, sells=90, mcap=250_000.0):
    return {
        "chainId": "solana", "dexId": dex,
        "liquidity": {"usd": liq}, "volume": {"h24": vol},
        "priceUsd": price, "pairCreatedAt": created, "marketCap": mcap,
        "txns": {"h24": {"buys": buys, "sells": sells}},
    }


def test_fresh_mints_solana_pump_first_deduped():
    mints = fetch_fresh_mints(get=lambda url: _profiles())
    assert [m["mint"] for m in mints] == ["AAAApump", "BBB111"]
    assert all(m["chain"] == "solana" for m in mints)


def test_fresh_mints_limit():
    mints = fetch_fresh_mints(limit=1, get=lambda url: _profiles())
    assert len(mints) == 1


def test_fresh_mints_feed_down_raises_not_guesses():
    def boom(url):
        raise ConnectionError("down")

    with pytest.raises(DiscoveryError):
        fetch_fresh_mints(get=boom)


def test_fresh_mints_non_list_raises():
    with pytest.raises(DiscoveryError):
        fetch_fresh_mints(get=lambda url: {"oops": True})


def test_enrich_picks_deepest_pool_and_age():
    data = {"pairs": [
        _pair("orca", liq=5_000.0, created=1_700_000_100_000),
        _pair("raydium", liq=50_000.0, created=1_700_000_000_000, vol=20_000.0),
    ]}
    out = enrich_mint("MINT", now_s=1_700_001_000.0, get=lambda url: data)
    assert out["liquidity_usd"] == 50_000.0
    assert out["volume_24h_usd"] == 20_000.0
    assert out["age_s"] == pytest.approx(1000.0)  # oldest pair wins
    assert out["graduated"] is True  # raydium pool exists
    assert out["venue"] == "raydium"
    assert out["txns_24h"] == {"buys": 100, "sells": 90}


def test_enrich_reports_best_pool_mcap():
    data = {"pairs": [
        _pair("orca", liq=5_000.0, mcap=9_000.0),
        _pair("raydium", liq=50_000.0, mcap=250_000.0),
    ]}
    out = enrich_mint("MINT", get=lambda url: data)
    assert out["mcap_usd"] == 250_000.0


def test_enrich_reports_best_pool_address():
    data = {"pairs": [
        dict(_pair("orca", liq=5_000.0), pairAddress="ORCAPOOL"),
        dict(_pair("raydium", liq=50_000.0), pairAddress="RAYPOOL"),
    ]}
    out = enrich_mint("MINT", get=lambda url: data)
    assert out["pool_address"] == "RAYPOOL"


def test_enrich_reports_symbol():
    data = {"pairs": [
        dict(_pair("orca", liq=5_000.0), baseToken={"symbol": "ORCA"}),
        dict(_pair("raydium", liq=50_000.0), baseToken={"symbol": "PEPE"}),
    ]}
    out = enrich_mint("MINT", get=lambda url: data)
    assert out["symbol"] == "PEPE"  # deepest pool's base token


def test_enrich_symbol_absent_is_none():
    out = enrich_mint("MINT", get=lambda url: {"pairs": [_pair()]})
    assert out["symbol"] is None


def test_enrich_mcap_absent_is_none():
    data = {"pairs": [{"chainId": "solana", "dexId": "raydium",
                       "liquidity": {"usd": 50_000.0}}]}
    out = enrich_mint("MINT", get=lambda url: data)
    assert out["mcap_usd"] is None


def test_enrich_ungraduated_when_no_venue_pool():
    data = {"pairs": [_pair("pump", liq=8_000.0)]}
    out = enrich_mint("MINT", get=lambda url: data)
    assert out["graduated"] is False


def test_enrich_no_solana_pairs_raises():
    with pytest.raises(DiscoveryError):
        enrich_mint("MINT", get=lambda url: {"pairs": []})


def test_enrich_null_pairs_raises_not_crashes():
    """VPS crash 2026-10-11: fresh mints return explicit null pairs
    (not indexed yet). Must raise DiscoveryError, never TypeError."""
    with pytest.raises(DiscoveryError):
        enrich_mint("MINT", get=lambda url: {"pairs": None})


def test_enrich_invalid_mint_raises():
    with pytest.raises(DiscoveryError):
        enrich_mint("  ", get=lambda url: {"pairs": []})


def test_discover_candidates_shape():
    out = discover_candidates(limit=2, get=lambda url: _profiles())
    assert len(out) == 2
