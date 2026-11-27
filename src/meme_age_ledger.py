"""Fixed-notional paper ledger for ``age_variant_v1`` matched trades."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path


FEE_PER_LEG = 0.0125
SLIPPAGE_TIERS = ((5_000.0, 0.08), (20_000.0, 0.04),
                  (100_000.0, 0.02), (float("inf"), 0.01))


def slippage_rate(liquidity_usd: float, notional_usd: float) -> float:
    """Conservative base slippage plus constant-product impact proxy."""
    if liquidity_usd <= 0 or notional_usd <= 0:
        raise ValueError("liquidity and notional must be positive")
    base = next(rate for ceiling, rate in SLIPPAGE_TIERS if liquidity_usd < ceiling)
    impact = min(1.0, notional_usd / liquidity_usd)
    return base + impact


@dataclass
class PaperTrade:
    key: str
    variant: str
    mint: str
    entry_ts: float
    entry_price: float
    notional_usd: float
    entry_liquidity_usd: float
    peak_price: float | None = None
    entry_volume_24h_usd: float | None = None
    entry_lp_reserve_usd: float | None = None
    exit_ts: float | None = None
    exit_price: float | None = None
    exit_liquidity_usd: float | None = None
    gross_pnl_usd: float | None = None
    costs_usd: float | None = None
    net_pnl_usd: float | None = None
    status: str = "open"
    failure_reason: str | None = None


class AgeVariantLedger:
    def __init__(self, *, notional_usd: float = 25.0,
                 max_open_positions: int = 3,
                 event_path: str | Path | None = None):
        self.notional_usd = float(notional_usd)
        self.max_open_positions = int(max_open_positions)
        self.event_path = Path(event_path) if event_path else None
        self.open: dict[str, PaperTrade] = {}
        self.closed: list[PaperTrade] = []

    def _record(self, event: str, trade: PaperTrade | None = None, **fields):
        if self.event_path is None:
            return
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        row = {"event": event, "ts": time.time(), **fields}
        if trade is not None:
            row["trade"] = asdict(trade)
        with self.event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    def open_trade(self, *, variant: str, mint: str, entry_ts: float,
                   entry_price: float, liquidity_usd: float,
                   volume_24h_usd: float | None = None,
                   lp_reserve_usd: float | None = None) -> PaperTrade:
        key = f"{variant}:{mint}"
        if key in self.open or any(t.key == key for t in self.closed):
            raise ValueError("duplicate mint/variant trade")
        if len(self.open) >= self.max_open_positions:
            raise RuntimeError("maximum open paper positions reached")
        if entry_price <= 0:
            raise ValueError("entry price must be positive")
        slippage_rate(liquidity_usd, self.notional_usd)
        trade = PaperTrade(key, variant, mint, float(entry_ts), float(entry_price),
                           self.notional_usd, float(liquidity_usd),
                           peak_price=float(entry_price),
                           entry_volume_24h_usd=volume_24h_usd,
                           entry_lp_reserve_usd=lp_reserve_usd)
        self.open[key] = trade
        self._record("paper_entry", trade)
        return trade

    def close_trade(self, *, variant: str, mint: str, exit_ts: float,
                    exit_price: float, liquidity_usd: float,
                    failure_reason: str | None = None) -> PaperTrade:
        key = f"{variant}:{mint}"
        trade = self.open.pop(key, None)
        if trade is None:
            raise KeyError("open paper trade not found")
        trade.exit_ts = float(exit_ts)
        trade.exit_price = float(exit_price)
        trade.exit_liquidity_usd = float(liquidity_usd)
        trade.failure_reason = failure_reason
        if failure_reason:
            trade.gross_pnl_usd = -trade.notional_usd
            trade.costs_usd = 0.0
            trade.net_pnl_usd = -trade.notional_usd
            trade.status = "failed_exit"
        else:
            entry_slip = slippage_rate(trade.entry_liquidity_usd, trade.notional_usd)
            exit_slip = slippage_rate(liquidity_usd, trade.notional_usd)
            trade.gross_pnl_usd = trade.notional_usd * (exit_price / trade.entry_price - 1.0)
            trade.costs_usd = trade.notional_usd * (entry_slip + exit_slip + 2 * FEE_PER_LEG)
            trade.net_pnl_usd = trade.gross_pnl_usd - trade.costs_usd
            trade.status = "closed"
        self.closed.append(trade)
        self._record("paper_exit", trade)
        return trade

    def summary(self) -> dict:
        return {"open": len(self.open), "closed": len(self.closed),
                "net_pnl_usd": round(sum((t.net_pnl_usd or 0.0) for t in self.closed), 6),
                "failed_exits": sum(t.status == "failed_exit" for t in self.closed)}
