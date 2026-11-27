import json
from pathlib import Path

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from scripts.transfer_gta_reserve import (DEST_EXPECTED, LAMPORTS, SOURCE_EXPECTED,
                                          load_key, build_transfer, preflight_balances,
                                          verify_destination_delta, verify_transfer_transaction)


def _write(path: Path, key: Keypair):
    path.write_text(json.dumps(list(bytes(key))), encoding="utf-8")


def test_keypair_public_key_allowlist(tmp_path):
    key = Keypair()
    path = tmp_path / "key.json"
    _write(path, key)
    with pytest.raises(RuntimeError, match="mismatch"):
        load_key(str(path), SOURCE_EXPECTED)


def test_transfer_instruction_is_exact_amount(tmp_path):
    source = Keypair()
    destination = Keypair()
    path = tmp_path / "source.json"
    _write(path, source)
    tx = build_transfer(source, destination.pubkey(), "11111111111111111111111111111111")
    assert len(tx.message.instructions) == 1
    assert LAMPORTS == 10_000_000


def test_preflight_requires_fee_reserve_and_empty_destination():
    preflight_balances(LAMPORTS + 5_000_000, 0)
    with pytest.raises(RuntimeError, match="insufficient"):
        preflight_balances(LAMPORTS + 4_999_999, 0)
    with pytest.raises(RuntimeError, match="destination"):
        preflight_balances(LAMPORTS + 5_000_000, 1)


def test_destination_delta_must_be_exact():
    verify_destination_delta(0, LAMPORTS)
    with pytest.raises(RuntimeError, match="delta"):
        verify_destination_delta(0, LAMPORTS - 1)


def test_transaction_verifier_accepts_only_expected_transfer():
    source = Keypair()
    destination = Keypair()
    blockhash = "11111111111111111111111111111111"
    tx = build_transfer(source, destination.pubkey(), blockhash)
    verify_transfer_transaction(tx, source.pubkey(), destination.pubkey(), blockhash)

