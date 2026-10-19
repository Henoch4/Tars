"""Blur firehose tests (hermetic — faked socket, no network, no keys)."""
import json

import pytest

from src.solami_blur import (
    BlurError,
    build_subscribe_message,
    collect_blur_candidates,
    parse_launch_event,
)


def test_subscribe_message_narrow_and_bounded():
    msg = json.loads(build_subscribe_message(backfill=50))
    assert set(msg["types"]) == {"token_create", "pool_create", "graduation"}
    assert msg["backfill"] == 50
    with pytest.raises(ValueError):
        build_subscribe_message(backfill=201)


def test_parse_single_events():
    assert parse_launch_event(
        {"type": "token_create", "mint": "M1", "name": "n"}) == [
            {"mint": "M1", "via": "token_create", "name": "n",
             "symbol": None, "backfill": False}]
    assert parse_launch_event(
        {"type": "graduation", "mint": "M2"})[0]["via"] == "graduation"
    assert parse_launch_event({"type": "token_create"}) == []  # no mint
    assert parse_launch_event({"type": "candle"}) == []  # not consumed
    assert parse_launch_event("garbage") == []
    assert parse_launch_event({}) == []


def test_parse_rejects_quote_mints():
    wsol = "So11111111111111111111111111111111111111112"
    usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    # pool_create for a SOL-paired pool reports the wSOL side — never a pick
    assert parse_launch_event({"type": "pool_create", "mint": wsol}) == []
    assert parse_launch_event({"type": "token_create", "mint": usdc}) == []
    out = parse_launch_event(
        {"type": "launches",
         "items": [{"mint": wsol}, {"mint": "Real1"}]})
    assert [c["mint"] for c in out] == ["Real1"]


def test_parse_list_snapshot():
    msg = {"type": "launches",
           "items": [{"mint": "A"}, {"mint": ""}, {"nope": 1}, "x"]}
    out = parse_launch_event(msg)
    assert [c["mint"] for c in out] == ["A"]
    assert out[0]["via"] == "launches"


def test_parse_rows_shaped_lists():
    # graduating/graduated snapshots carry rows under "rows", not "items"
    # (live shape, probe 2026-10-14). Same mint extraction, same guards.
    msg = {"type": "graduated", "event": "snapshot",
           "rows": [{"mint": "G1", "symbol": "X"}, {"no_mint": 1}]}
    out = parse_launch_event(msg)
    assert [c["mint"] for c in out] == ["G1"]
    assert out[0]["via"] == "graduated"
    assert parse_launch_event({"type": "graduating", "rows": "nope"}) == []
    assert parse_launch_event({"type": "graduated"}) == []


@pytest.mark.asyncio
async def test_collect_dedupes_and_counts():
    async def fake_stream():
        for m in [
            {"type": "launches", "items": [{"mint": "A"}, {"mint": "B"}]},
            {"type": "token_create", "mint": "B", "backfill": True},
            {"type": "token_create", "mint": "C"},
            {"type": "candle", "mint": "X"},
            {"type": "weird"},
        ]:
            yield m

    store = await collect_blur_candidates("k", connect_fn=fake_stream)
    assert [c["mint"] for c in store["mints"]] == ["A", "B", "C"]
    assert store["counts"]["launches"] == 1
    assert store["counts"]["candle"] == 1
    assert store["skipped"] == 2  # candle + weird


@pytest.mark.asyncio
async def test_collect_requires_key():
    with pytest.raises(BlurError):
        await collect_blur_candidates("  ")


@pytest.mark.asyncio
async def test_unparsed_counts_want_types_yielding_zero():
    async def fake_stream():
        for m in [
            {"type": "graduated", "items": [{"no_mint": 1}]},
            {"type": "graduated", "items": [{"mint": "G"}]},
            {"type": "candle"},
        ]:
            yield m

    store = await collect_blur_candidates("k", connect_fn=fake_stream)
    assert store["unparsed"] == {"graduated": 1}
    assert [c["mint"] for c in store["mints"]] == ["G"]
    # skipped semantics untouched (non-WANT with zero parses only)
    assert store["skipped"] == 1


@pytest.mark.asyncio
async def test_on_raw_sees_every_message():
    seen = []

    async def fake_stream():
        for m in [{"type": "token_create", "mint": "A"}, {"type": "candle"}]:
            yield m

    await collect_blur_candidates("k", connect_fn=fake_stream,
                                  on_raw=seen.append)
    assert [m["type"] for m in seen] == ["token_create", "candle"]
