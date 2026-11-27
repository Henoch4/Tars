import pytest
from src.gta_route_state import compare_route_accounts, resolve_v0_keys, expand_v0_instruction_accounts


def test_route_accounts_match_manifest():
    keys = [{"address":"a","owner":"o","executable":False,"data_length":3,"data_hash":"h"}]
    assert compare_route_accounts(keys, {"a":{"owner":"o","executable":False,"data_length":3,"data_hash":"h"}})["complete"]


def test_route_accounts_fail_on_missing_or_mismatch():
    keys = [{"address":"a","owner":"wrong"},{"address":"a"},{"address":"b"}]
    result = compare_route_accounts(keys, {"a":{"owner":"o"}})
    assert result["complete"] is False
    assert result["live_rpc_used"] is False


def test_v0_lookup_resolution_preserves_order_and_hashes():
    table = "lookup"
    snapshot = {table: {"owner": "AddressLookupTab1e1111111111111111111111111",
                        "addresses": ["w", "r"]}}
    result = resolve_v0_keys(["static"], [{"table_address": table,
                                           "writable_indexes": [0],
                                           "readonly_indexes": [1]}], snapshot)
    assert result["keys"] == ["static", "w", "r"]
    assert result["live_rpc_used"] is False


def test_v0_lookup_missing_or_duplicate_fails():
    with pytest.raises(RuntimeError):
        resolve_v0_keys(["static"], [{"table_address": "missing"}], {})
    with pytest.raises(RuntimeError):
        resolve_v0_keys(["a"], [{"table_address": "t", "writable_indexes": [0]}],
                        {"t": {"owner": "AddressLookupTab1e1111111111111111111111111", "addresses": ["a"]}})


def test_v0_loaded_indices_are_expanded_not_null():
    result = expand_v0_instruction_accounts(
        [{"program_index": 2, "account_indices": [0, 3]}],
        ["static", "program", "alt-writable", "alt-readonly"],
    )
    assert [a["address"] for a in result[0]["accounts"]] == ["static", "alt-readonly"]


def test_created_accounts_and_jupiter_sentinel_are_not_false_missing_state():
    keys = [{"address":"created"}, {"address":"jitodontfront1111111111111111JustUseJupiter"}]
    result = compare_route_accounts(
        keys, {}, created_in_transaction={"created"},
        runtime_sentinels={"jitodontfront1111111111111111JustUseJupiter"})
    assert result["complete"] is True
