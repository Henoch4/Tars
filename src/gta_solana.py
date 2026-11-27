"""Read-only Solana token-account balance reader for GTA reconciliation."""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


SOLANA_MAINNET_RPC = "https://api.mainnet-beta.solana.com"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


@dataclass(frozen=True)
class TokenBalance:
    mint: str
    owner: str
    amount_raw: int
    decimals: int


def _default_post(rpc_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        rpc_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise RuntimeError("Solana RPC returned non-object")
    return body


def read_token_balance(
    mint: str,
    owner: str,
    *,
    rpc_url: str = SOLANA_MAINNET_RPC,
    post: Callable[[str, dict[str, Any]], dict[str, Any]] = _default_post,
) -> TokenBalance:
    """Read the exact raw balance for one mint owned by one wallet."""
    if not isinstance(mint, str) or not mint.strip() or not isinstance(owner, str) or not owner.strip():
        raise ValueError("mint and owner are required")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [owner, {"programId": TOKEN_PROGRAM_ID}, {"encoding": "jsonParsed"}],
    }
    response = post(rpc_url, payload)
    if response.get("error") is not None:
        raise RuntimeError(f"Solana RPC error: {response['error']}")
    rows = ((response.get("result") or {}).get("value"))
    if not isinstance(rows, list):
        raise RuntimeError("Solana RPC token-account response malformed")
    total = 0
    decimals: int | None = None
    for row in rows:
        try:
            info = row["account"]["data"]["parsed"]["info"]
            if info.get("mint") != mint:
                continue
            token_amount = info["tokenAmount"]
            amount = int(token_amount["amount"])
            row_decimals = int(token_amount["decimals"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Solana RPC token-account row malformed") from exc
        if amount < 0 or row_decimals < 0:
            raise RuntimeError("Solana RPC returned invalid token amount")
        total += amount
        decimals = row_decimals if decimals is None else decimals
        if decimals != row_decimals:
            raise RuntimeError("token accounts disagree on decimals")
    return TokenBalance(mint=mint, owner=owner, amount_raw=total, decimals=decimals or 0)
