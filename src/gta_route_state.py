"""Deterministic route-account comparison for synthetic replay."""
from __future__ import annotations
import hashlib
from typing import Any, AbstractSet


def compare_route_accounts(account_keys: list[dict[str, Any]], snapshot: dict[str, dict[str, Any]],
                           *, created_in_transaction: AbstractSet[str] = frozenset(),
                           runtime_sentinels: AbstractSet[str] = frozenset()) -> dict[str, Any]:
    """Compare decoded transaction account metadata against captured state."""
    mismatches = []
    seen = set()
    for key in account_keys:
        address = key.get("address")
        if not isinstance(address, str) or address in seen:
            mismatches.append({"address": address, "reason": "duplicate_or_invalid_key"})
            continue
        seen.add(address)
        state = snapshot.get(address)
        if not isinstance(state, dict):
            if address in created_in_transaction:
                continue
            if address in runtime_sentinels:
                continue
            mismatches.append({"address": address, "reason": "missing_snapshot_account"})
            continue
        for field in ("owner", "executable", "data_length"):
            if field in key and state.get(field) != key[field]:
                mismatches.append({"address": address, "reason": field + "_mismatch"})
        for field in ("is_signer", "is_writable"):
            if field in key and state.get(field) != key[field]:
                mismatches.append({"address": address, "reason": field + "_mismatch"})
        expected_hash = state.get("data_hash")
        actual_hash = key.get("data_hash")
        if expected_hash and actual_hash and expected_hash != actual_hash:
            mismatches.append({"address": address, "reason": "data_hash_mismatch"})
    return {"complete": not mismatches, "account_count": len(seen), "mismatches": mismatches,
            "source": "captured_snapshot", "live_rpc_used": False}


def resolve_v0_keys(static_keys: list[str], lookups: list[dict[str, Any]],
                    snapshot: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Resolve v0 loaded addresses from captured lookup-table state only."""
    resolved = list(static_keys)
    for table in lookups:
        address = table.get("table_address")
        if not isinstance(address, str):
            raise RuntimeError("missing lookup table address")
        state = snapshot.get(address)
        if not isinstance(state, dict) or state.get("owner") != "AddressLookupTab1e1111111111111111111111111":
            raise RuntimeError("missing or invalid lookup table")
        entries = state.get("addresses")
        if not isinstance(entries, list):
            raise RuntimeError("malformed lookup table")
        for index in table.get("writable_indexes", ()):
            if not isinstance(index, int) or index < 0 or index >= len(entries):
                raise RuntimeError("lookup index out of range")
            resolved.append(entries[index])
        for index in table.get("readonly_indexes", ()):
            if not isinstance(index, int) or index < 0 or index >= len(entries):
                raise RuntimeError("lookup index out of range")
            resolved.append(entries[index])
    if len(resolved) != len(set(resolved)):
        raise RuntimeError("duplicate resolved account")
    digest = hashlib.sha256("|".join(resolved).encode()).hexdigest()
    return {"keys": resolved, "message_hash": digest, "live_rpc_used": False}


def expand_v0_instruction_accounts(instructions: list[dict[str, Any]], resolved_keys: list[str]) -> list[dict[str, Any]]:
    """Bind instruction account indices to the already snapshot-resolved v0 key list.

    Versioned messages store static keys separately from ALT-loaded keys; treating
    loaded indices as null creates false missing-account failures. This helper is
    deliberately pure and refuses out-of-range/duplicate indices.
    """
    out = []
    for ix_no, ix in enumerate(instructions):
        indices = ix.get("account_indices", ())
        if not isinstance(indices, list):
            raise ValueError("malformed instruction account indices")
        accounts = []
        for index in indices:
            if not isinstance(index, int) or index < 0 or index >= len(resolved_keys):
                raise ValueError("instruction account index out of range")
            accounts.append({"address": resolved_keys[index], "index": index})
        out.append({"instruction": ix_no, "program_index": ix.get("program_index"), "accounts": accounts})
    return out
