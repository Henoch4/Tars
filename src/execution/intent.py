"""Language-neutral execution contract.

Strategies create intents; a future isolated signer/broadcaster consumes them.
This module deliberately contains no wallet, RPC, or transaction code.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Literal
import json
import uuid

Action = Literal["buy", "sell", "transfer"]
IntentStatus = Literal["planned", "refused", "simulated", "submitted", "confirmed", "failed"]


class IntentRefused(ValueError):
    """Raised when an intent violates the execution boundary."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ExecutionIntent:
    intent_id: str
    venue: str
    network: str
    action: Action
    asset: str
    amount: str
    max_slippage_bps: int
    dry_run: bool = True
    allow_live: bool = False
    operator_approved: bool = False
    created_at: str = ""
    source: str = "strategy"

    def __post_init__(self) -> None:
        if not self.intent_id:
            raise IntentRefused("intent_id is required")
        if not self.venue or not self.network or not self.asset:
            raise IntentRefused("venue, network, and asset are required")
        if self.action not in ("buy", "sell", "transfer"):
            raise IntentRefused("unsupported action")
        try:
            amount = float(self.amount)
        except (TypeError, ValueError):
            raise IntentRefused("amount must be numeric") from None
        if amount <= 0:
            raise IntentRefused("amount must be positive")
        if not 0 <= self.max_slippage_bps <= 10_000:
            raise IntentRefused("max_slippage_bps must be between 0 and 10000")
        if self.allow_live and self.dry_run:
            raise IntentRefused("allow_live cannot be combined with dry_run")
        if self.allow_live and not self.operator_approved:
            raise IntentRefused("live intent requires explicit operator approval")

    @classmethod
    def create(cls, *, venue: str, network: str, action: Action, asset: str,
               amount: str, max_slippage_bps: int, source: str = "strategy",
               dry_run: bool = True, allow_live: bool = False,
               operator_approved: bool = False) -> "ExecutionIntent":
        return cls(uuid.uuid4().hex, venue, network, action, asset, amount,
                   max_slippage_bps, dry_run, allow_live, operator_approved,
                   _now(), source)

    def to_dict(self) -> dict:
        return asdict(self)

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ExecutionReceipt:
    intent_id: str
    status: IntentStatus
    signed: bool = False
    broadcast: bool = False
    provider: str = ""
    signature: str | None = None
    reason: str | None = None
    observed_at: str = ""

    def __post_init__(self) -> None:
        if self.signed and not self.broadcast:
            raise ValueError("a signed receipt must declare whether it was broadcast")
        if self.broadcast and not self.signature:
            raise ValueError("broadcast receipt requires a signature")
        if self.status in ("refused", "failed") and not self.reason:
            raise ValueError("refused/failed receipt requires a reason")

    @classmethod
    def refused(cls, intent_id: str, reason: str, provider: str = "policy") -> "ExecutionReceipt":
        return cls(intent_id, "refused", reason=reason, provider=provider, observed_at=_now())

    def to_dict(self) -> dict:
        return asdict(self)

