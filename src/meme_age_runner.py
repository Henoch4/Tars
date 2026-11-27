"""Matched-pair runner connecting ``age_variant_v1`` decisions to paper ledgers."""
from __future__ import annotations

from dataclasses import dataclass

from .meme_age_experiment import AgeExperimentConfig, evaluate_pair
from .meme_age_ledger import AgeVariantLedger
from .meme_satellite import MarketUpdate, TokenSnapshot, evaluate_exit
from .strategy_lifecycle import LifecycleState, LifecycleStatus


@dataclass
class RunnerResult:
    decision: dict
    opened: tuple[str, ...]


class _ExitView:
    def __init__(self, trade):
        self.entry_price = trade.entry_price
        self.entry_ts = trade.entry_ts
        self.peak_price = trade.peak_price or trade.entry_price
        self.entry_window_volume_usd = trade.entry_volume_24h_usd or 0.0


class AgeVariantRunner:
    """Runs baseline and age-variant paper books on matched snapshots."""

    def __init__(self, config: AgeExperimentConfig = AgeExperimentConfig(),
                 *, baseline_ledger: AgeVariantLedger | None = None,
                 variant_ledger: AgeVariantLedger | None = None):
        self.config = config
        self.baseline = baseline_ledger or AgeVariantLedger(
            notional_usd=config.paper_notional_usd,
            max_open_positions=config.max_open_positions)
        self.variant = variant_ledger or AgeVariantLedger(
            notional_usd=config.paper_notional_usd,
            max_open_positions=config.max_open_positions)
        self._lifecycle = LifecycleState.READY
        self._blocked_reason: str | None = None

    def status(self) -> LifecycleStatus:
        return LifecycleStatus("age-variant", self._lifecycle, True, False,
                               blocked_reason=self._blocked_reason)

    def start(self) -> None:
        self._lifecycle = LifecycleState.RUNNING
        self._blocked_reason = None

    def pause(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("pause reason required")
        self._lifecycle = LifecycleState.PAUSED
        self._blocked_reason = reason

    def stop(self, reason: str = "") -> None:
        self._lifecycle = LifecycleState.STOPPED
        self._blocked_reason = reason or None

    def ingest(self, snapshot: TokenSnapshot, *, observed_at: float) -> RunnerResult:
        decision = evaluate_pair(snapshot, observed_at=observed_at, config=self.config)
        opened: list[str] = []
        entry_price = snapshot.price_usd
        liquidity = snapshot.liquidity_usd
        if not isinstance(entry_price, (int, float)) or entry_price <= 0:
            return RunnerResult(decision, tuple(opened))
        if not isinstance(liquidity, (int, float)) or liquidity <= 0:
            return RunnerResult(decision, tuple(opened))
        for name, book, result in (("baseline", self.baseline, decision["baseline"]),
                                   ("variant", self.variant, decision["variant"])):
            if result["passed"]:
                try:
                    book.open_trade(variant=name, mint=snapshot.mint,
                                    entry_ts=observed_at, entry_price=entry_price,
                                    liquidity_usd=liquidity)
                    opened.append(name)
                except (RuntimeError, ValueError):
                    decision.setdefault("paper_rejections", []).append(name)
        return RunnerResult(decision, tuple(opened))

    def close(self, mint: str, *, exit_price: float, liquidity_usd: float,
              exit_ts: float, failure_reason: str | None = None) -> list[dict]:
        results = []
        for name, book in (("baseline", self.baseline), ("variant", self.variant)):
            if f"{name}:{mint}" in book.open:
                results.append(book.close_trade(variant=name, mint=mint,
                    exit_ts=exit_ts, exit_price=exit_price,
                    liquidity_usd=liquidity_usd,
                    failure_reason=failure_reason).__dict__)
        return results

    def mark(self, mint: str, *, price: float, liquidity_usd: float,
             ts: float, volume_24h_usd: float | None = None,
             lp_reserve_usd: float | None = None,
             sell_tax_pct_now: float | None = None,
             minutes_since_last: float = 1.0,
             failed_sell_reason: str | None = None) -> list[dict]:
        """Apply one identical market update and close triggered paper trades."""
        decisions: list[dict] = []
        for name, book in (("baseline", self.baseline), ("variant", self.variant)):
            trade = book.open.get(f"{name}:{mint}")
            if trade is None:
                continue
            trade.peak_price = max(trade.peak_price or trade.entry_price, price)
            view = _ExitView(trade)
            view.peak_price = trade.peak_price
            decision, reason = evaluate_exit(view, MarketUpdate(
                price=price, volume_24h_usd=volume_24h_usd,
                lp_reserve_usd=lp_reserve_usd,
                lp_reserve_at_entry_usd=trade.entry_lp_reserve_usd,
                sell_tax_pct_now=sell_tax_pct_now, ts=ts,
                minutes_since_last=minutes_since_last))
            if decision == "exit":
                closed = self.close(mint, exit_price=price,
                    liquidity_usd=liquidity_usd, exit_ts=ts,
                    failure_reason=failed_sell_reason)
                decisions.extend({"variant": row["variant"], "reason": reason, **row} for row in closed)
            else:
                decisions.append({"variant": name, "status": "hold", "reason": reason, "mint": mint})
        return decisions

    def summary(self) -> dict:
        return {"baseline": self.baseline.summary(), "variant": self.variant.summary()}
