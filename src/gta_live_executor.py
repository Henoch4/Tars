"""Gated live adapter for the GTA position book.

The adapter is intentionally separate from discovery and position accounting.
It requires the event policy to be armed *and* a non-empty operator confirm
string before calling the underlying Jupiter functions. Tests inject fake
functions; production wiring can use ``meme_execute.live_buy/live_sell``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Any
from dataclasses import dataclass

from .gta_book import GtaBook
from .meme_execute import ExecutionLedger, SOL_MINT, live_buy, live_sell, book2_pubkey


@dataclass(frozen=True)
class FlattenReport:
    reason: str
    attempted: int
    closed: int
    failed: int
    residual_mints: tuple[str, ...]
    results: tuple[dict[str, Any], ...]


class GtaLiveExecutor:
    def __init__(
        self,
        book: GtaBook,
        *,
        keypath: str,
        ledger: ExecutionLedger,
        buy_fn: Callable[..., dict[str, Any]] = live_buy,
        sell_fn: Callable[..., dict[str, Any]] = live_sell,
        pubkey_fn: Callable[[str], str] = book2_pubkey,
        balance_reader: Callable[[str], int] | None = None,
    ):
        self.book = book
        self.keypath = keypath
        self.ledger = ledger
        self.buy_fn = buy_fn
        self.sell_fn = sell_fn
        self.pubkey_fn = pubkey_fn
        self.balance_reader = balance_reader

    def enter(
        self,
        candidate: dict[str, Any],
        *,
        sol_price_usd: float,
        confirm: str = "",
        now_ts: float | None = None,
    ) -> dict[str, Any]:
        """Attempt one live entry; no confirmation means no broadcast."""
        import time

        ts = time.time() if now_ts is None else float(now_ts)
        armed, reason = self.book.policy.broadcast_allowed(
            datetime.fromtimestamp(ts, timezone.utc)
        )
        if not armed:
            return {"broadcast": False, "reason": reason}
        if not confirm or not confirm.strip():
            return {"broadcast": False, "reason": "live confirmation required"}
        if not isinstance(sol_price_usd, (int, float)) or sol_price_usd <= 0:
            return {"broadcast": False, "reason": "SOL price unavailable"}
        allowed, reason = self.book.authorize_entry(candidate, now_ts=ts)
        if not allowed:
            return {"broadcast": False, "reason": reason}
        size_usd = float(candidate["size_usd"])
        amount_raw = int(size_usd / float(sol_price_usd) * 1_000_000_000)
        if amount_raw <= 0:
            return {"broadcast": False, "reason": "entry amount below one lamport"}
        taker = self.pubkey_fn(self.keypath)
        result = self.buy_fn(
            SOL_MINT,
            str(candidate["mint"]),
            amount_raw,
            taker,
            self.keypath,
            float(candidate.get("expected_costs_usd", 0.0)),
            size_usd,
            self.ledger,
            slippage_bps=int(candidate.get("slippage_bps", 100)),
            confirm=confirm,
        )
        if result.get("broadcast"):
            # State changes only after the venue reports a broadcast receipt.
            self.book.record_entry(candidate, now_ts=ts)
        return result

    def exit(
        self,
        mint: str,
        *,
        amount_raw: int | None = None,
        confirm: str = "",
        net_usd: float = 0.0,
        slippage_bps: int = 100,
    ) -> dict[str, Any]:
        """Attempt a full live exit and acknowledge only a broadcast fill."""
        if mint not in self.book.positions:
            return {"broadcast": False, "reason": "unknown GTA position"}
        if not confirm or not confirm.strip():
            return {"broadcast": False, "reason": "live confirmation required"}
        position = self.book.positions.get(mint)
        if position is None:
            return {"broadcast": False, "reason": "unknown GTA position"}
        if amount_raw is None:
            amount_raw = position.token_amount_raw
        if isinstance(amount_raw, bool) or not isinstance(amount_raw, int) or amount_raw <= 0:
            return {"broadcast": False, "reason": "invalid token amount"}
        if self.balance_reader is None:
            return {"broadcast": False, "reason": "post-fill balance reader required"}
        taker = self.pubkey_fn(self.keypath)
        result = self.sell_fn(
            mint,
            SOL_MINT,
            amount_raw,
            taker,
            self.keypath,
            slippage_bps=slippage_bps,
            confirm=confirm,
            ledger=self.ledger,
        )
        if result.get("broadcast"):
            remaining_raw = self.balance_reader(mint)
            if remaining_raw == 0:
                self.book.acknowledge_exit(mint, net_usd=net_usd)
            else:
                self.book.acknowledge_partial_exit(
                    mint, remaining_raw=remaining_raw, net_usd=net_usd,
                )
            result["post_fill_balance_raw"] = remaining_raw
        return result

    def flatten_all(self, *, confirm: str = "", reason: str = "emergency") -> FlattenReport:
        """Best-effort full close, then leave unresolved exposure visible."""
        if self.balance_reader is None:
            result = {"mint": "*", "broadcast": False, "reason": "balance reader required"}
            return FlattenReport(reason, 0, 0, 1, tuple(self.book.positions), (result,))
        results: list[dict[str, Any]] = []
        closed = 0
        residual: list[str] = []
        for mint in list(self.book.positions):
            try:
                raw = self.balance_reader(mint)
                if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
                    raise ValueError("invalid reconciled balance")
                if raw == 0:
                    self.book.acknowledge_exit(mint, net_usd=0.0)
                    result = {"mint": mint, "broadcast": False, "closed": True, "reason": "already flat on chain"}
                else:
                    result = self.exit(mint, amount_raw=raw, confirm=confirm, net_usd=0.0, slippage_bps=800)
                    result = {"mint": mint, **result}
                results.append(result)
                if mint in self.book.positions:
                    residual.append(mint)
                else:
                    closed += 1
            except Exception as exc:  # continue flattening other mints
                result = {"mint": mint, "broadcast": False, "closed": False,
                          "reason": f"flatten error: {type(exc).__name__}: {exc}"}
                results.append(result)
                residual.append(mint)
        failed = len(results) - closed
        return FlattenReport(reason, len(results), closed, failed, tuple(residual), tuple(results))
