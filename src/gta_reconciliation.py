"""Chain-truth reconciliation for the isolated GTA position book."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ReconciliationResult:
    ok: bool
    paused: bool
    reason: str
    balances: dict[str, int]


def reconcile_token_balances(
    local_positions: dict[str, Any],
    read_balance: Callable[[str], int | None],
) -> ReconciliationResult:
    """Compare every local position with its on-chain raw token balance.

    ``None`` means the chain read failed or was unavailable and therefore
    pauses entries. A zero balance is valid only when the local position is
    also explicitly zeroed; unknown on-chain positions are a mismatch.
    """
    balances: dict[str, int] = {}
    for mint, position in local_positions.items():
        try:
            raw = read_balance(mint)
        except Exception as exc:  # transport failure: pause, never guess zero
            return ReconciliationResult(False, True, f"balance read failed for {mint}: {type(exc).__name__}", balances)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            return ReconciliationResult(False, True, f"invalid balance for {mint}", balances)
        balances[mint] = raw
        expected = getattr(position, "token_amount_raw", None)
        if expected is None:
            return ReconciliationResult(False, True, f"local token amount missing for {mint}", balances)
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            return ReconciliationResult(False, True, f"invalid local token amount for {mint}", balances)
        if raw != expected:
            return ReconciliationResult(False, True, f"balance mismatch for {mint}: chain={raw} local={expected}", balances)
    return ReconciliationResult(True, False, "reconciliation passed", balances)
