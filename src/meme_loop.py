"""Satellite orchestrator — the continuous loop that ties everything together.

Cycle: discover → enrich → screen → enter (paper or live) → monitor open
positions → exit on trigger → record PnL. Runs as a persistent loop with
configurable interval, or one round at a time for testing/demo.

Paper-first doctrine (G1): entries default to paper. Live entries require
an explicit confirm string (the second deliberate act). The loop tracks
open positions against deterministic exits (rug sentinel, time box, peak
drawdown, dead tape, tax trap) and records every decision in a JSONL
blotter that persists across restarts.

This module does NOT hold keys, make network calls on import, or move
funds. The executor (meme_execute.py) is called only when explicitly
wired in with a confirm.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .meme_satellite import (
    EXIT_PRICE_DROP_PCT,
    MAX_HOLD_S,
    MarketUpdate,
    PaperBlotter,
    PaperPosition,
    TokenSnapshot,
    clamp_size,
    evaluate_exit,
    scream_filter,
)

logger = logging.getLogger(__name__)

# Persistence
BLOTTER_PATH = "data/meme_satellite/blotter.jsonl"
POSITIONS_PATH = "data/meme_satellite/positions.json"

# Loop defaults
DEFAULT_INTERVAL_S = 60.0
DEFAULT_MAX_CANDIDATES = 10

# Screening budget: how much of the session cap each entry uses
ENTRY_SIZE_USD = 25.0

# Re-entry cooldown (2026-10-18): after a mint exits — win or loss — block
# re-entry for a fixed window so the loop can't chop the same name back and
# forth. Blotter evidence: one mint was entered/exited 4x in ~3h (3 losses)
# because try_enter only deduped against OPEN positions. A rug-sentinel exit
# or a washout loss (>=25% of size) earns a longer window.
REENTRY_COOLDOWN_S = 45 * 60.0
RUG_REENTRY_COOLDOWN_S = 135 * 60.0
RUG_REENTRY_LOSS_FRAC = 0.25


@dataclass
class OpenPosition:
    """A live paper position tracked across cycles."""

    mint: str
    symbol: str
    entry_price: float
    entry_usd: float
    size_usd: float
    entry_ts: float
    entry_window_volume_usd: float
    peak_price: float
    last_price: float
    last_volume_24h_usd: float | None = None
    lp_reserve_usd: float | None = None
    lp_reserve_at_entry_usd: float | None = None
    sell_tax_pct_now: float | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "OpenPosition":
        return cls(**d)

    def to_market_update(self, ts: float, minutes_since_last: float = 1.0) -> MarketUpdate:
        return MarketUpdate(
            price=self.last_price,
            volume_24h_usd=self.last_volume_24h_usd,
            lp_reserve_usd=self.lp_reserve_usd,
            lp_reserve_at_entry_usd=self.lp_reserve_at_entry_usd,
            sell_tax_pct_now=self.sell_tax_pct_now,
            ts=ts,
            minutes_since_last=minutes_since_last,
        )


class SatelliteLoop:
    """The satellite brain: discover, screen, enter, monitor, exit, record."""

    def __init__(
        self,
        blotter_path: str = BLOTTER_PATH,
        positions_path: str = POSITIONS_PATH,
        entry_size_usd: float = ENTRY_SIZE_USD,
        max_session_usd: float = 100.0,
        interval_s: float = DEFAULT_INTERVAL_S,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        halt_path: str | None = None,
        reentry_cooldown_s: float = REENTRY_COOLDOWN_S,
        rug_reentry_cooldown_s: float = RUG_REENTRY_COOLDOWN_S,
        rug_reentry_loss_frac: float = RUG_REENTRY_LOSS_FRAC,
        breakers=None,
        on_entry=None,
        on_exit=None,
    ):
        self.blotter_path = Path(blotter_path)
        self.positions_path = Path(positions_path)
        # Kill switch (Book-2 satellite): a HALT file beside the positions
        # file blocks NEW entries while exits keep running (existing risk
        # stays managed). Operator creates it (touch HALT); removal resumes.
        # Checked every cycle, survives restarts by construction.
        self.halt_path = Path(halt_path) if halt_path else \
            self.positions_path.parent / "HALT"
        self._halted = False
        self.entry_size_usd = entry_size_usd
        self.max_session_usd = max_session_usd
        self.interval_s = interval_s
        self.max_candidates = max_candidates
        self.reentry_cooldown_s = float(reentry_cooldown_s)
        self.rug_reentry_cooldown_s = float(rug_reentry_cooldown_s)
        self.rug_reentry_loss_frac = float(rug_reentry_loss_frac)
        # Optional layers, DEFAULT OFF (None) so paper decisions stay frozen
        # while G1 is measured. Wiring lands post-G1: `breakers` = portfolio
        # circuit breakers (src/satellite_breakers); on_entry/on_exit = live
        # execution hooks (meme_execute live_buy/live_sell). All inert at None.
        self.breakers = breakers
        self.on_entry = on_entry
        self.on_exit = on_exit
        self.blotter = PaperBlotter()
        self.open_positions: dict[str, OpenPosition] = {}
        # mint -> {"ts": exit_ts, "cooldown_s": window, "reason": exit_reason}
        self.reentry_cooldown: dict[str, dict] = {}
        self.spent_usd = 0.0
        self.realized_net_usd = 0.0
        self.cycle_count = 0
        self._load_state()

    # -- persistence --------------------------------------------------

    def _load_state(self):
        """Restore open positions + session spend from disk (crash recovery)."""
        self.positions_path.parent.mkdir(parents=True, exist_ok=True)
        if self.positions_path.exists():
            try:
                data = json.loads(self.positions_path.read_text())
                for d in data.get("positions", []):
                    pos = OpenPosition.from_dict(d)
                    self.open_positions[pos.mint] = pos
                # A persisted residue can be tiny-negative from float
                # add/subtract; it is not real spend and must not survive a
                # restart as garbage (clamp_size fails closed on negative).
                raw_spent = data.get("spent_usd", 0.0)
                try:
                    raw_spent = float(raw_spent)
                except (TypeError, ValueError):
                    raw_spent = 0.0
                if not math.isfinite(raw_spent) or raw_spent < 0:
                    raw_spent = 0.0
                self.spent_usd = round(raw_spent, 6)
                self.realized_net_usd = data.get("realized_net_usd", 0.0)
                self.cycle_count = data.get("cycle_count", 0)
                if self.breakers is not None and isinstance(data.get("breakers"), dict):
                    from .satellite_breakers import SatelliteBreakers
                    self.breakers = SatelliteBreakers.from_state(
                        data["breakers"], self.breakers.limits)
                # Re-entry cooldowns survive restarts; expired ones drop.
                now = time.time()
                for mint, cd in (data.get("reentry_cooldown") or {}).items():
                    if not isinstance(cd, dict):
                        continue
                    try:
                        until = float(cd.get("ts", 0.0)) + float(cd.get("cooldown_s", 0.0))
                    except (TypeError, ValueError):
                        continue
                    if until > now:
                        self.reentry_cooldown[mint] = cd
                logger.info(
                    "restored %d open positions, $%.2f spent",
                    len(self.open_positions), self.spent_usd)
            except Exception as e:
                logger.warning("could not restore positions: %s", e)

    def _save_state(self):
        try:
            self.positions_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "positions": [p.to_dict() for p in self.open_positions.values()],
                "spent_usd": self.spent_usd,
                "realized_net_usd": self.realized_net_usd,
                "cycle_count": self.cycle_count,
                "reentry_cooldown": self.reentry_cooldown,
                "updated_at": time.time(),
            }
            if self.breakers is not None:
                payload["breakers"] = self.breakers.state()
            self.positions_path.write_text(json.dumps(payload, indent=2))
        except Exception as e:
            logger.warning("could not save positions: %s", e)

    def _record(self, record: dict):
        """Append one JSONL record (audit trail for the satellite)."""
        self.blotter_path.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.time()
        record["cycle"] = self.cycle_count
        with open(self.blotter_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    # -- cycle steps ---------------------------------------------------

    def screen_candidates(self, snapshots: list[TokenSnapshot]) -> list[TokenSnapshot]:
        """Run the scream filter on enriched snapshots. Returns PASSes."""
        passed = []
        for snap in snapshots:
            verdict = scream_filter(snap)
            self._record({
                "event": "screen",
                "mint": snap.mint,
                "passed": verdict.passed,
                "rejected_by": verdict.rejected_by,
                "reasons": verdict.reasons,
                # Creator-history bootstrap: creator + rugged verdict ride
                # along for future per-deployer aggregation. Additive keys;
                # readers must tolerate their absence on pre-change rows.
                "creator_address": getattr(snap, "creator_address", None),
                "rugcheck_rugged": getattr(snap, "rugcheck_rugged", None),
            })
            if verdict.passed:
                passed.append(snap)
        return passed

    def try_enter(self, snap: TokenSnapshot, price_usd: float,
                  liquidity_usd: float, volume_24h_usd: float) -> bool:
        """Attempt entry on a screened token. Paper by default. Returns
        True if entered (paper or live)."""
        if snap.mint in self.open_positions:
            return False  # already in
        # Re-entry cooldown: a recently-exited mint stays blocked until its
        # window closes. Prevents churning a name that just dumped (the
        # blotter's 4x re-entry pattern). Expired entries are swept here.
        now = time.time()
        cd = self.reentry_cooldown.get(snap.mint)
        if cd:
            until = float(cd.get("ts", 0.0)) + float(cd.get("cooldown_s", 0.0))
            if now < until:
                self._record({
                    "event": "entry_blocked",
                    "mint": snap.mint,
                    "reason": "re-entry cooldown",
                    "remaining_s": round(until - now, 1),
                    "orig_exit_reason": cd.get("reason"),
                })
                return False
            del self.reentry_cooldown[snap.mint]
        if self.breakers is not None:
            open_exposure = sum(p.size_usd for p in self.open_positions.values())
            ok, reason = self.breakers.new_entries_allowed(
                len(self.open_positions), open_exposure, now)
            if not ok:
                self._record({"event": "entry_blocked", "mint": snap.mint,
                              "reason": f"breaker: {reason}"})
                return False
        if self.spent_usd + self.entry_size_usd > self.max_session_usd:
            self._record({"event": "entry_blocked", "mint": snap.mint,
                          "reason": "session cap"})
            return False
        if liquidity_usd <= 0 or price_usd <= 0:
            self._record({"event": "entry_blocked", "mint": snap.mint,
                          "reason": "price/liquidity unobserved",
                          "price_usd": price_usd,
                          "liquidity_usd": liquidity_usd})
            return False

        from src.meme_satellite import adaptive_entry_size
        proposed = adaptive_entry_size(self.entry_size_usd, liquidity_usd,
                                       volume_24h_usd)
        if proposed <= 0:
            self._record({"event": "entry_blocked", "mint": snap.mint,
                          "reason": "adaptive size below dust line"})
            return False
        allowed = clamp_size(proposed, self.spent_usd)
        if allowed <= 0:
            # Never silent: a zero clamp used to stop all entries with no
            # trace (window #3: a -3.6e-15 spend residue killed 12.5h).
            self._record({"event": "entry_blocked", "mint": snap.mint,
                          "reason": "size clamp returned 0",
                          "proposed_usd": round(proposed, 4),
                          "spent_usd": round(self.spent_usd, 6)})
            return False

        pos = OpenPosition(
            mint=snap.mint,
            symbol=(snap.symbol or "").strip(),  # ticker; display-only, "" if unobserved
            entry_price=price_usd,
            entry_usd=allowed,
            size_usd=allowed,
            entry_ts=time.time(),
            entry_window_volume_usd=volume_24h_usd,
            peak_price=price_usd,
            last_price=price_usd,
            lp_reserve_at_entry_usd=liquidity_usd,
            lp_reserve_usd=liquidity_usd,
        )
        self.open_positions[snap.mint] = pos
        self.spent_usd = round(self.spent_usd + allowed, 6)
        entry_record = {
            "event": "entry",
            "mint": snap.mint,
            "size_usd": allowed,
            "price": price_usd,
            "sized_from_liquidity_usd": liquidity_usd,
            "sized_from_volume_usd": volume_24h_usd,
            "paper": True,  # live entries set this False via executor
        }
        self._record(entry_record)
        if self.on_entry is not None:
            self._safe_hook(self.on_entry, entry_record)
        self._save_state()
        return True

    def check_exits(self, price_updates: dict[str, dict]) -> list[dict]:
        """Check all open positions against deterministic exits.
        price_updates: {mint: {price, volume_24h_usd, lp_reserve_usd, ...}}
        Returns list of exit records."""
        exits = []
        now = time.time()
        for mint, pos in list(self.open_positions.items()):
            upd = price_updates.get(mint)
            if not upd:
                continue
            price = upd.get("price", pos.last_price)
            if price <= 0:
                continue

            # Update peak + last known values
            if price > pos.peak_price:
                pos.peak_price = price
            pos.last_price = price
            pos.last_volume_24h_usd = upd.get("volume_24h_usd")
            if upd.get("lp_reserve_usd") is not None:
                pos.lp_reserve_usd = upd["lp_reserve_usd"]
            if upd.get("sell_tax_pct_now") is not None:
                pos.sell_tax_pct_now = upd["sell_tax_pct_now"]

            # Run the exit checks
            mu = pos.to_market_update(now)
            decision, reason = evaluate_exit(pos, mu)
            if decision == "exit":
                # Paper exit: calculate net PnL
                gross = pos.size_usd * (price / pos.entry_price - 1.0) if pos.entry_price > 0 else -pos.size_usd
                costs = pos.size_usd * 0.01 * 2 + 0.5  # PAPER_SLIPPAGE_PCT * 2 + gas
                net = gross - costs
                self.realized_net_usd += net
                record = {
                    "event": "exit",
                    "mint": mint,
                    "reason": reason,
                    "entry_price": pos.entry_price,
                    "exit_price": price,
                    "peak_price": pos.peak_price,
                    "size_usd": pos.size_usd,
                    "gross_usd": round(gross, 4),
                    "costs_usd": round(costs, 4),
                    "net_usd": round(net, 4),
                    "hold_time_s": round(now - pos.entry_ts, 0),
                }
                exits.append(record)
                self._record(record)
                if self.breakers is not None:
                    self.breakers.record_result(net, now)
                if self.on_exit is not None:
                    self._safe_hook(self.on_exit, record)
                # Arm the re-entry cooldown: rug-sentinel exits and washouts
                # (loss >= frac of size) get the longer window.
                _rug = str(reason).upper().startswith("RUG")
                _washout = net <= -self.rug_reentry_loss_frac * pos.size_usd
                self.reentry_cooldown[mint] = {
                    "ts": now,
                    "cooldown_s": (self.rug_reentry_cooldown_s
                                   if (_rug or _washout)
                                   else self.reentry_cooldown_s),
                    "reason": reason,
                }
                # Float residue must never go negative: clamp_size reads a
                # negative spend as garbage and stops every entry.
                self.spent_usd = max(0.0, round(self.spent_usd - pos.size_usd, 6))
                del self.open_positions[mint]

        if exits:
            self._save_state()
        return exits

    def portfolio(self) -> dict:
        """Current state snapshot: open positions, PnL, session status."""
        unrealized = 0.0
        for pos in self.open_positions.values():
            if pos.entry_price > 0:
                unrealized += pos.size_usd * (pos.last_price / pos.entry_price - 1.0)
        realized = self.realized_net_usd
        return {
            "cycle_count": self.cycle_count,
            "open_positions": len(self.open_positions),
            "spent_usd": round(self.spent_usd, 2),
            "max_session_usd": self.max_session_usd,
            "unrealized_usd": round(unrealized, 4),
            "realized_net_usd": round(realized, 4),
            "total_net_usd": round(unrealized + realized, 4),
            "positions": {
                m: {
                    "symbol": p.symbol or m[:8],
                    "entry": p.entry_price,
                    "last": p.last_price,
                    "peak": p.peak_price,
                    "size": p.size_usd,
                    "unrealized": round(p.size_usd * (p.last_price / p.entry_price - 1.0), 4)
                        if p.entry_price > 0 else 0,
                    "hold_h": round((time.time() - p.entry_ts) / 3600, 1),
                }
                for m, p in self.open_positions.items()
            },
        }

    def halted(self) -> bool:
        """Kill-switch state, read live every call (file, not memory)."""
        try:
            return self.halt_path.exists()
        except OSError:
            return False

    def _alert(self, event: str, severity: str, detail: str) -> None:
        """Best-effort webhook alert. No-op unless ALERT_WEBHOOK_URL is set
        (send_alert returns False with zero overhead). Never raises into the
        loop — alerting must not become a new failure mode."""
        try:
            from .alerting import send_alert
            send_alert(event, severity, detail)
        except Exception:  # noqa: BLE001
            logger.warning("alert dispatch failed: %s", event)

    def _safe_hook(self, hook, record: dict) -> None:
        """Invoke an optional wiring hook (live execution). A failing hook
        must never break the loop or change its recorded decisions."""
        try:
            hook(record)
        except Exception:  # noqa: BLE001
            logger.warning("hook %r failed", getattr(hook, "__name__", hook),
                           exc_info=True)

    def run_cycle(self, snapshots: list[TokenSnapshot],
                  price_updates: dict[str, dict] | None = None) -> dict:
        """One cycle: screen → enter → check exits → report.
        snapshots: enriched TokenSnapshots from the feed.
        price_updates: current prices for open positions (from any source).
        HALT active: screen + enter skipped (no new risk); exits still run."""
        self.cycle_count += 1
        halted = self.halted()
        if halted and not self._halted:
            self._record({"event": "halt_engaged"})
            self._alert("SATELLITE_HALT_ENGAGED", "warning",
                        "kill switch engaged: new entries blocked")
        if not halted and self._halted:
            self._record({"event": "halt_released"})
            self._alert("SATELLITE_HALT_RELEASED", "info",
                        "kill switch released: entries resume")
        self._halted = halted
        passed: list = []
        entries: list = []
        screened = 0
        if not halted:
            passed = self.screen_candidates(snapshots)
            screened = len(snapshots)
            for snap in passed[:self.max_candidates]:
                # Price and liquidity come from enrichment (DexScreener/Blur)
                # or the snapshot's operator extras — caller supplies them.
                price = getattr(snap, "_price_usd", None) or 0
                liq = snap.liquidity_usd or 0
                vol = getattr(snap, "_volume_24h_usd", None) or 0
                if price > 0 and self.try_enter(snap, price, liq, vol):
                    entries.append(snap.mint)
        exits = self.check_exits(price_updates or {})
        self._save_state()
        return {
            "cycle": self.cycle_count,
            "screened": screened,
            "passed": len(passed),
            "entered": entries,
            "exited": [e["mint"] for e in exits],
            "exit_details": exits,
            "halted": halted,
            "portfolio": self.portfolio(),
        }
