"""Paper execution adapter for the isolated GTA position book.

It exercises the same entry/exit lifecycle a future live adapter must use,
but never loads a key or calls a network endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass

from .gta_book import GtaBook


@dataclass(frozen=True)
class PaperReceipt:
    broadcast: bool
    side: str
    mint: str
    size_usd: float
    reason: str


class GtaPaperExecutor:
    def __init__(self, book: GtaBook):
        self.book = book

    def enter(self, candidate: dict, now_ts: float | None = None) -> PaperReceipt:
        pos = self.book.record_entry(candidate, now_ts=now_ts)
        return PaperReceipt(False, "buy", pos.mint, pos.size_usd, "paper fill")

    def mark(self, mint: str, price: float, now=None) -> PaperReceipt | None:
        decision = self.book.exit_decision(mint, price, now=now)
        if decision.action in ("hold", "halt"):
            return None
        pos = self.book.positions[mint]
        sell_size = pos.size_usd * decision.sell_pct / 100.0
        # Paper accounting acknowledges the fill immediately. A live adapter
        # must wait for confirmed venue settlement before acknowledging.
        entry = pos.entry_price
        net = sell_size * (float(price) / entry - 1.0)
        if decision.action == "sell_all":
            self.book.acknowledge_exit(mint, net_usd=net)
        else:
            # Partial exits are represented as a paper reduction; the live
            # adapter must persist the exact filled tranche and remaining size.
            pos.size_usd = max(0.0, pos.size_usd - sell_size)
            for idx, fired in enumerate(pos.ladder_fired):
                if not fired:
                    pos.ladder_fired[idx] = True
                    break
            self.book._save()
        return PaperReceipt(False, "sell", mint, sell_size, decision.reason)
