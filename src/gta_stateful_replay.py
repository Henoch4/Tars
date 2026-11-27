"""Stateful, read-only buy/sell replay boundary.

The backend is intentionally injected so fixtures and a local validator can
share the same fail-closed accounting contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
import time


@dataclass(frozen=True)
class ReplayResult:
    status: str  # passed, blocked, unavailable
    reason: str | None = None
    effective_loss_pct: float | None = None
    buy_output_raw: int | None = None
    sell_output_raw: int | None = None
    buy_slot: int | None = None
    sell_slot: int | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


class ReplayBackend(Protocol):
    def apply_buy(self, transaction: str) -> dict[str, Any]: ...
    def build_sell(self, token_amount_raw: int) -> str: ...
    def simulate_sell(self, transaction: str) -> dict[str, Any]: ...


def validate_snapshot(snapshot: dict[str, Any], *, now: float | None = None,
                      max_age_s: int = 900, runtime_version: str | None = None) -> None:
    """Validate representative mainnet state before a replay is allowed."""
    if not isinstance(snapshot, dict):
        raise RuntimeError("snapshot is not an object")
    required = ("source_identity", "slot", "block_height", "commitment",
                "observed_at", "runtime_version", "program_hashes", "mint",
                "pool", "programs", "accounts")
    if any(key not in snapshot for key in required):
        raise RuntimeError("snapshot incomplete")
    if snapshot.get("provenance_mode") not in ("mainnet_equivalent", "synthetic_replay"):
        raise RuntimeError("invalid provenance mode")
    if snapshot.get("provenance_mode") == "synthetic_replay" and snapshot.get("ledger_slot") != 0:
        raise RuntimeError("synthetic replay ledger slot must be zero")
    if snapshot.get("provenance_mode") == "mainnet_equivalent" and snapshot.get("ledger_slot") != snapshot.get("slot"):
        raise RuntimeError("mainnet-equivalent ledger slot mismatch")
    if not isinstance(snapshot["slot"], int) or snapshot["slot"] < 0:
        raise RuntimeError("invalid snapshot slot")
    if not isinstance(snapshot["block_height"], int) or snapshot["block_height"] < 0:
        raise RuntimeError("invalid snapshot block height")
    if snapshot["commitment"] != "finalized":
        raise RuntimeError("snapshot is not finalized")
    if not isinstance(snapshot["source_identity"], str) or not snapshot["source_identity"].strip():
        raise RuntimeError("snapshot source missing")
    if not isinstance(snapshot["runtime_version"], str) or not snapshot["runtime_version"].strip():
        raise RuntimeError("runtime version missing")
    if not isinstance(snapshot["program_hashes"], dict) or not snapshot["program_hashes"]:
        raise RuntimeError("program hashes missing")
    runtime = snapshot.get("runtime_dependency")
    if not isinstance(runtime, dict) or runtime.get("runtime_provided") is not True:
        raise RuntimeError("runtime dependency contract missing")
    if not runtime.get("loader_identity") or not runtime.get("validator_version"):
        raise RuntimeError("runtime dependency identity missing")
    if runtime.get("compatibility") is not True:
        raise RuntimeError("runtime compatibility not verified")
    current = time.time() if now is None else float(now)
    observed = snapshot["observed_at"]
    if not isinstance(observed, (int, float)) or current - observed < 0 or current - observed > max_age_s:
        raise RuntimeError("stale snapshot")
    if runtime_version is not None and snapshot.get("runtime_version") != runtime_version:
        raise RuntimeError("program/runtime version mismatch")
    if not isinstance(snapshot["programs"], (list, tuple)) or not snapshot["programs"]:
        raise RuntimeError("required programs missing")
    if not isinstance(snapshot["accounts"], (list, tuple)) or not snapshot["accounts"]:
        raise RuntimeError("required accounts missing")
    if snapshot.get("conflict") is True:
        raise RuntimeError("snapshot evidence conflict")


def replay_round_trip(*, backend: ReplayBackend, buy_transaction: str,
                      quote_amount_raw: int, state_source: str = "local_validator") -> ReplayResult:
    provenance = {"state_source": state_source, "simulation_only": True,
                  "signed": False, "broadcast": False}
    if not isinstance(buy_transaction, str) or not buy_transaction:
        return ReplayResult("unavailable", "missing buy transaction", provenance=provenance)
    try:
        buy = backend.apply_buy(buy_transaction)
    except Exception as exc:
        return ReplayResult("unavailable", f"buy_replay:{type(exc).__name__}", provenance=provenance)
    if not isinstance(buy, dict) or buy.get("ok") is False:
        return ReplayResult("blocked", "buy replay failed", provenance=provenance)
    token_amount = buy.get("token_amount_raw")
    if not isinstance(token_amount, int) or token_amount <= 0:
        return ReplayResult("unavailable", "token account/output missing", provenance=provenance)
    try:
        sell_tx = backend.build_sell(token_amount)
        sell = backend.simulate_sell(sell_tx)
    except Exception as exc:
        return ReplayResult("unavailable", f"sell_replay:{type(exc).__name__}",
                            buy_output_raw=token_amount, buy_slot=buy.get("slot"),
                            provenance=provenance)
    if not isinstance(sell, dict) or sell.get("ok") is False:
        return ReplayResult("blocked", "sell replay failed", buy_output_raw=token_amount,
                            buy_slot=buy.get("slot"), sell_slot=sell.get("slot") if isinstance(sell, dict) else None,
                            provenance=provenance)
    output = sell.get("quote_output_raw")
    if not isinstance(output, int) or output < 0 or not isinstance(quote_amount_raw, int) or quote_amount_raw <= 0:
        return ReplayResult("unavailable", "sell output missing", buy_output_raw=token_amount,
                            buy_slot=buy.get("slot"), provenance=provenance)
    loss = round(max(0.0, min(100.0, (1 - output / quote_amount_raw) * 100)), 6)
    return ReplayResult("passed", effective_loss_pct=loss, buy_output_raw=token_amount,
                        sell_output_raw=output, buy_slot=buy.get("slot"),
                        sell_slot=sell.get("slot"), provenance=provenance)
