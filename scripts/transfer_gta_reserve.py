"""Single-purpose, allowlisted SOL reserve transfer.

Dry-run by default. This command only permits the exact 0.01 SOL transfer
from the configured satellite keypair to the configured GTA keypair.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.message import Message
from solders.transaction import Transaction
from solders.hash import Hash
from solders.system_program import ID as SYSTEM_PROGRAM_ID

LAMPORTS = 10_000_000
SOURCE_EXPECTED = "38hpURXXM1CM9Ugo4aWYkckM4H36jH2BXX5wZWz2MbzY"
DEST_EXPECTED = "Ehfkx3c8aGV6xGbvmSezHvBLt68ZwWdw1bvSXZShcwUu"
FEE_RESERVE_LAMPORTS = 5_000_000


def load_key(path: str, expected: str) -> Keypair:
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(values, list) or len(values) != 64 or any(not isinstance(x, int) for x in values):
        raise RuntimeError("invalid keypair format")
    key = Keypair.from_bytes(bytes(values))
    if str(key.pubkey()) != expected:
        raise RuntimeError("keypair public key mismatch")
    return key


def build_transfer(source: Keypair, destination: Pubkey, recent_blockhash: str) -> Transaction:
    instruction = transfer(TransferParams(from_pubkey=source.pubkey(), to_pubkey=destination,
                                          lamports=LAMPORTS))
    message = Message([instruction], source.pubkey())
    tx = Transaction([source], message, Hash.from_string(recent_blockhash))
    if len(tx.message.instructions) != 1:
        raise RuntimeError("unexpected instruction count")
    return tx


def preflight_balances(source_lamports: int, destination_lamports: int,
                       *, amount: int = LAMPORTS,
                       fee_reserve: int = FEE_RESERVE_LAMPORTS) -> None:
    if source_lamports < amount + fee_reserve:
        raise RuntimeError("insufficient source balance plus fee reserve")
    if destination_lamports != 0:
        raise RuntimeError("destination is not empty")


def verify_destination_delta(before: int, after: int, *, amount: int = LAMPORTS) -> None:
    if after - before != amount:
        raise RuntimeError("destination balance delta mismatch")


def verify_transfer_transaction(tx: Transaction, source: Pubkey, destination: Pubkey,
                                 recent_blockhash: str, *, amount: int = LAMPORTS) -> None:
    """Reject every transaction shape except one system-program transfer."""
    msg = tx.message
    if str(msg.recent_blockhash) != recent_blockhash:
        raise RuntimeError("recent blockhash mismatch")
    if msg.account_keys[0] != source:
        raise RuntimeError("fee payer mismatch")
    if len(msg.instructions) != 1:
        raise RuntimeError("unexpected instruction count")
    ix = msg.instructions[0]
    program = msg.account_keys[ix.program_id_index]
    if program != SYSTEM_PROGRAM_ID:
        raise RuntimeError("non-system program instruction")
    if bytes(ix.data)[:4] != bytes([2, 0, 0, 0]) or len(ix.data) != 12:
        raise RuntimeError("instruction is not System Transfer")
    lamports = int.from_bytes(bytes(ix.data)[4:], "little")
    if lamports != amount:
        raise RuntimeError("transfer amount mismatch")
    if len(ix.accounts) != 2:
        raise RuntimeError("unexpected transfer accounts")
    if msg.account_keys[ix.accounts[0]] != source or msg.account_keys[ix.accounts[1]] != destination:
        raise RuntimeError("transfer account mismatch")
    if len(tx.signatures) != 1:
        raise RuntimeError("unexpected signer count")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="/etc/tars/book2-satellite.json")
    ap.add_argument("--destination", default="/etc/tars/gta-wallet.json")
    ap.add_argument("--rpc-url", required=True)
    ap.add_argument("--broadcast", action="store_true", help="required to send the exact transfer")
    args = ap.parse_args()
    source = load_key(args.source, SOURCE_EXPECTED)
    destination_key = load_key(args.destination, DEST_EXPECTED)
    print(json.dumps({"status": "preflight", "source": str(source.pubkey()),
                      "destination": str(destination_key.pubkey()),
                      "lamports": LAMPORTS, "broadcast": bool(args.broadcast)}))
    if not args.broadcast:
        return 0
    from solana.rpc.api import Client
    from solana.rpc.types import TxOpts
    from solana.rpc.commitment import Finalized
    client = Client(args.rpc_url)
    source_before = int(client.get_balance(source.pubkey(), commitment=Finalized).value)
    destination_before = int(client.get_balance(destination_key.pubkey(), commitment=Finalized).value)
    preflight_balances(source_before, destination_before)
    latest = client.get_latest_blockhash(commitment=Finalized).value
    blockhash = str(latest.blockhash)
    tx = build_transfer(source, destination_key.pubkey(), blockhash)
    verify_transfer_transaction(tx, source.pubkey(), destination_key.pubkey(), blockhash)
    if str(tx.message.recent_blockhash) != blockhash:
        raise RuntimeError("pre-submit blockhash assertion failed")
    response = client.send_transaction(
        tx, opts=TxOpts(skip_preflight=False, preflight_commitment=Finalized))
    signature = str(response.value)
    status = client.confirm_transaction(response.value, commitment=Finalized)
    if not status.value:
        raise RuntimeError("finalized confirmation failed")
    source_after = int(client.get_balance(source.pubkey(), commitment=Finalized).value)
    destination_after = int(client.get_balance(destination_key.pubkey(), commitment=Finalized).value)
    verify_destination_delta(destination_before, destination_after)
    print(json.dumps({"status": "finalized", "source": str(source.pubkey()),
                      "destination": str(destination_key.pubkey()),
                      "lamports": LAMPORTS, "signature": signature,
                      "source_before": source_before, "source_after": source_after,
                      "destination_before": destination_before,
                      "destination_after": destination_after,
                      "commitment": "finalized"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
