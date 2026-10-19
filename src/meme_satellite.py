"""Memecoin satellite engine v1 — paper-only novelty/rule filter (Book 2).

Implements docs/alpha-mode-memecoins.md §4 + the Bober/zostaff stealables
(docs/STEALABLES.md): scream filter (reject ~95% pre-buy, SKIP default),
structural entry gating, deterministic pivot exits, time-boxed holds, RUG
sentinel, post-model size clamp, reproducibility (seeded, deterministic).

HARD WALLS (not configurable):
- Paper only: this module holds no keys, makes no network calls, and has no
  swap/send path. It scores snapshots and records would-be trades. Any
  future execution wiring lives outside it and behind the G-gates.
- SKIP default: a snapshot missing a check's inputs is REJECTED, never
  default-passed. An unverifiable token is indistinguishable from a bad one.
- Book 2 only: personal capital, hard-capped, separate EOA — enforced when
  capital connects, declared here so the wall predates the money.

Feed mapping (callers supply snapshots; $0 sources per the plan):
  holder concentration <- holder distribution (Bitquery/poll holders)
  lp_locked <- LP lock state / burn (LP lock events)
  mint/freeze authority <- token mint metadata (None = renounced = pass)
  age_s <- pair creation timestamp
  liquidity_usd <- DEX pool reserves (size/liquidity slippage, not bps)
  honeypot flags <- simulation / tax read (Jupiter round-trip quote at
    entry size: transfer hooks and sell-disable show as sell-leg loss)
  creator history <- deployer past-rug count
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# Scream-filter thresholds (policy, visible in one place; tightening them
# only ever rejects more — the safe direction for this book).
MAX_TOP10_HOLD_PCT = 50.0  # top-10 holders share above this: concentrated
MAX_CREATOR_HOLD_PCT = 10.0  # deployer/sniper-cluster share above this: insider-heavy
MIN_LIQUIDITY_USD = 20_000.0  # pool below this: un-exitable size
MIN_MCAP_USD = 10_000.0  # placeholder not-dead line (a live survivor read
# $25.7k post-decay; graduation implies ~$40-70k, so 10k only kills dust).
# Calibrate against survivor mcap distribution when observed — research
# suggests 1-1.5x graduation MC, which needs per-token grad-MC data.
MIN_AGE_S = 6 * 3600  # younger than this: sniper/bot window, not a market
MAX_BUY_TAX_PCT = 5.0  # buy tax above this: configured against the trader
MAX_SELL_TAX_PCT = 10.0  # sell tax above this: exit may be the trap
MAX_ROUND_TRIP_COST_PCT = 15.0  # Jupiter round-trip at entry size above
# this: either a tax/honeypot or impact that eats the whole edge (the
# measured paper drag envelope is 5-15% at $25 — at 15%+ no trade works).
MAX_PAST_RUGS_CREATOR = 0  # any prior deployer rug: instant reject
MIN_HOLDERS = 10  # fewer visible holders: top10% is vacuous (top-10 of 6
# holders is trivially ~100%), not a concentration signal. 10 is the
# provably-vacuous cutoff given the RPC page; a true holder count (DAS
# pagination) would support a higher, distribution-grounded floor.

# Deterministic exits
EXIT_PRICE_DROP_PCT = 25.0  # peak-to-current drop that pivots out
EXIT_VOLUME_COLLAPSE_RATIO = 0.25  # 24h volume below 25% of entry window: dead tape
MAX_HOLD_S = 7 * 24 * 3600  # hard time box: no bag-holding as strategy
RUG_LP_DRAIN_PCT = 50.0  # LP reserve drop that trips the sentinel
RUG_PRICE_VELOCITY_PCT_PER_MIN = 30.0  # price collapse rate that trips it
# Profit ratchet (the ONE rule change of this window, 2026-10-19): the
# trail tightens once in profit, so fresh-graduate winners are defended
# like the live playbook's break-even doctrine intends. Below trigger the
# base -25% trail stands; after a >=+25% PEAK gain the giveback line moves
# to -10% off peak (worst path still nets ~+12.5%). Nothing else moves.
PROFIT_RATCHET_TRIGGER_GAIN_PCT = 25.0  # peak-vs-entry gain that arms the ratchet
PROFIT_RATCHET_TIGHTENED_DROP_PCT = 10.0  # trailing line after it is armed

# Post-model size clamp (second, independent cap after any sizing logic)
PER_TOKEN_CAP_USD = 100.0
PER_DAY_CAP_USD = 250.0

# Paper cost realism (charged on every would-be round trip)
PAPER_SLIPPAGE_PCT = 1.0  # AMM impact proxy at satellite size; real routing sizes it exactly
PAPER_GAS_USD = 0.5  # Solana priority-fee-era round trip estimate


@dataclass
class TokenSnapshot:
    """One candidate token as observed. None = unobserved (not zero)."""

    mint: str
    symbol: str | None = None  # ticker (DexScreener baseToken); display only
    top10_hold_pct: float | None = None
    holder_count: int | None = None  # visible holders (RPC page, max ~20)
    creator_address: str | None = None  # deployer (RugCheck; future history/bundle)
    rugcheck_rugged: bool | None = None  # independent rugged verdict
    rugcheck_score: float | None = None  # informational only, never gated
    creator_hold_pct: float | None = None
    lp_locked: bool | None = None
    mint_authority_renounced: bool | None = None
    freeze_authority_renounced: bool | None = None
    age_s: float | None = None
    liquidity_usd: float | None = None
    buy_tax_pct: float | None = None
    sell_tax_pct: float | None = None
    round_trip_cost_pct: float | None = None  # Jupiter quote buy+sell @ $25
    organic_score: float | None = None  # Jupiter organic/bundled read (note only)
    creator_past_rugs: int | None = None
    creator_launches: int | None = None  # Jupiter dev_mints (serial deployer; note only)
    graduated: bool = False  # bonding-curve graduation / survival confirmation
    mcap_usd: float | None = None  # best-pool market cap (DexScreener)
    block0_buyers: int | None = None  # distinct buyers in creation slot
    dev_sold_minute1: bool | None = None  # creator sold <=60s after creation


@dataclass
class ScreenResult:
    """Scream-filter verdict: pass only when every check passes."""

    passed: bool
    rejected_by: str | None = None  # first failing check (audit order below)
    reasons: list = field(default_factory=list)  # all evaluated notes


def _num(value, name: str, errors: list) -> float | None:
    """Strict numeric read: bool/string/NaN/inf are missing data, not zeros."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{name} unobserved (type {type(value).__name__})")
        return None
    if not math.isfinite(float(value)):
        errors.append(f"{name} non-finite")
        return None
    return float(value)


def scream_filter(snap: TokenSnapshot) -> ScreenResult:
    """Screen, evidence-ordered (2026-10 restructure from grounded research +
    900-cycle live diagnosis): holder floor (metric precondition) ->
    survival confirmation (the structural gate) -> creator history ->
    concentration (on real holder bases) -> taxes/round-trip sellability
    -> liquidity (our exitability). LP lock / mint+freeze authorities /
    age are INFORMATIONAL notes only — measured non-predictive (99%+ of
    confirmed rugs pass them; age filters fast winners). First failing
    evaluated note lands in reasons."""
    reasons: list = []

    def reject(check: str, note: str) -> ScreenResult:
        reasons.append(note)
        return ScreenResult(passed=False, rejected_by=check, reasons=list(reasons))

    # Gate 0 — independent rugged verdict (RugCheck). An external TRUE is
    # acted on; None/missing never passes anything by itself (every gate
    # below still applies). Fail-closed direction: rejects more, never less.
    if snap.rugcheck_rugged is True:
        reasons.append("RugCheck reports rugged: confirmed dead, do not touch")
        return ScreenResult(passed=False, rejected_by="rugged_confirmed", reasons=reasons)
    if snap.rugcheck_rugged is False:
        reasons.append("RugCheck: not flagged rugged")
    else:
        reasons.append("rugcheck verdict unobserved")

    # Precondition of the concentration metric: with fewer holders than the
    # top-N window, top10% is ~100% by construction. Reject as inapplicable
    # (too_few_holders), never as "concentrated" — mislabeling emptiness
    # as insider control would poison every diagnosis built on these codes.
    holders = snap.holder_count
    if holders is None or isinstance(holders, bool):
        reasons.append("holder_count unobserved")
        return ScreenResult(passed=False, rejected_by="missing_data", reasons=reasons)
    if holders < MIN_HOLDERS:
        reasons.append(f"only {holders} holders visible: concentration % inapplicable")
        return ScreenResult(passed=False, rejected_by="too_few_holders", reasons=reasons)
    reasons.append(f"{holders} holders visible: concentration measurable")

    # Gate 1 — survival confirmation FIRST (doc 4.2 + research strategy 1):
    # newborns die here on the meaningful check, not on vacuous metrics.
    if not snap.graduated:
        reasons.append("no survival confirmation (graduation/liquidity floor)")
        return ScreenResult(passed=False, rejected_by="unconfirmed", reasons=reasons)
    reasons.append("survival confirmed: structural entry allowed")

    # Gate 2 — creator history. Policy 2026-10-17 (research Q1): a KNOWN
    # prior rug still hard-rejects; an UNKNOWN (no supplier for this wallet
    # yet) is a NOTE, not a fatal gap — the same demotion already applied to
    # lp_locked / authorities / age after they measured non-predictive. The
    # field is fed by deployer_history (compounds over time). Before this
    # change, absence alone rejected 2,854 of 3,233 missing_data rows.
    rugs = snap.creator_past_rugs
    if isinstance(rugs, bool) or not isinstance(rugs, int):
        reasons.append("creator_history unobserved (no deployer record yet)")
    elif rugs > MAX_PAST_RUGS_CREATOR:
        return reject("creator_history", f"deployer has {rugs} past rug(s)")
    else:
        reasons.append("deployer clean")
    # Serial-deployer note (Jupiter dev_mints): a wallet with many prior
    # launches is a risk signal, but promoting it to a gate waits for a
    # measured distribution (one change at a time). Note only for now.
    launches = snap.creator_launches
    if isinstance(launches, int) and not isinstance(launches, bool):
        reasons.append(f"deployer launch count {launches} (note only)")

    # Gate 3 — concentration, now on a real holder base.
    top10 = _num(snap.top10_hold_pct, "top10_hold_pct", reasons)
    if top10 is None:
        return ScreenResult(passed=False, rejected_by="missing_data", reasons=reasons)
    if top10 > MAX_TOP10_HOLD_PCT:
        return reject("holder_concentration",
                       f"top10 holds {top10:.1f}% (> {MAX_TOP10_HOLD_PCT:.0f}%)")
    reasons.append(f"top10 holds {top10:.1f}%: dispersed enough")

    creator = _num(snap.creator_hold_pct, "creator_hold_pct", reasons)
    if creator is None:
        return ScreenResult(passed=False, rejected_by="missing_data", reasons=reasons)
    if creator > MAX_CREATOR_HOLD_PCT:
        return reject("insider_heavy", f"creator cluster holds {creator:.1f}%")
    reasons.append(f"creator cluster holds {creator:.1f}%: acceptable")

    # Gate 4 — taxes / sellability. Observed tax fields gate when a
    # supplier provides them. Else the measured Jupiter round-trip at
    # entry size is the honest equivalent (pool fees + price impact +
    # transfer hooks — the round-trip invariant run live before money
    # moves; sell-leg refusal = 100% = trap). Neither -> missing_data.
    buy_tax = _num(snap.buy_tax_pct, "buy_tax_pct", reasons)
    sell_tax = _num(snap.sell_tax_pct, "sell_tax_pct", reasons)
    if buy_tax is not None and sell_tax is not None:
        if buy_tax > MAX_BUY_TAX_PCT:
            return reject("honeypot_tax", f"buy tax {buy_tax:.1f}%")
        if sell_tax > MAX_SELL_TAX_PCT:
            return reject("honeypot_tax", f"sell tax {sell_tax:.1f}%")
        reasons.append(f"taxes buy {buy_tax:.1f}% / sell {sell_tax:.1f}%: not a trap")
    else:
        rt = _num(snap.round_trip_cost_pct, "round_trip_cost_pct", reasons)
        if rt is None:
            return ScreenResult(passed=False, rejected_by="missing_data",
                                reasons=reasons)
        if rt > MAX_ROUND_TRIP_COST_PCT:
            return reject("honeypot_tax",
                          f"round-trip cost {rt:.1f}% (> {MAX_ROUND_TRIP_COST_PCT:.0f}%)")
        reasons.append(f"round-trip cost {rt:.1f}% @ $25: sellable")

    # Gate 5 — liquidity floor (OUR exitability for $25 size, not prediction).
    liq = _num(snap.liquidity_usd, "liquidity_usd", reasons)
    if liq is None:
        return ScreenResult(passed=False, rejected_by="missing_data", reasons=reasons)
    if liq < MIN_LIQUIDITY_USD:
        return reject("thin_liquidity", f"pool ${liq:,.0f} (< ${MIN_LIQUIDITY_USD:,.0f})")
    reasons.append(f"pool ${liq:,.0f}: exitable")

    # Gate 5b — market-cap not-dead line (post-graduation only in effect:
    # non-graduates already died at Gate 1). Placeholder bar; calibrate
    # against the survivor distribution when observed.
    mcap = _num(snap.mcap_usd, "mcap_usd", reasons)
    if mcap is None:
        return ScreenResult(passed=False, rejected_by="missing_data", reasons=reasons)
    if mcap < MIN_MCAP_USD:
        return reject("thin_mcap", f"mcap ${mcap:,.0f} (< ${MIN_MCAP_USD:,.0f})")
    reasons.append(f"mcap ${mcap:,.0f}: not dead")

    # Notes only — measured non-predictive, never gates (see docstring).
    if snap.lp_locked is True:
        reasons.append("LP locked")
    else:
        reasons.append("LP not locked" if snap.lp_locked is False else "lp_locked unobserved")
    for field_name, label in (("mint_authority_renounced", "mint"),
                              ("freeze_authority_renounced", "freeze")):
        val = getattr(snap, field_name)
        if val is True:
            reasons.append(f"{label} authority renounced")
        else:
            reasons.append(f"{label} authority "
                           + ("unobserved" if val is None else "retained"))
    age = _num(snap.age_s, "age_s", reasons)
    if age is not None:
        reasons.append(f"age {age / 3600:.1f}h (informational)")
    if snap.organic_score is not None:
        reasons.append(f"jupiter organic score {snap.organic_score:.0f} (informational)")

    reasons.append("all gates passed")
    return ScreenResult(passed=True, rejected_by=None, reasons=reasons)


@dataclass
class PaperPosition:
    """A would-be position. No keys, no orders — arithmetic only."""

    mint: str
    entry_usd: float
    entry_price: float
    peak_price: float
    entry_ts: float
    entry_window_volume_usd: float
    size_usd: float


@dataclass
class MarketUpdate:
    """One observation of an open would-be position."""

    price: float
    volume_24h_usd: float | None = None
    lp_reserve_usd: float | None = None
    lp_reserve_at_entry_usd: float | None = None
    sell_tax_pct_now: float | None = None
    ts: float = 0.0
    minutes_since_last: float = 60.0


def check_rug(pos: PaperPosition, upd: MarketUpdate) -> tuple[bool, str]:
    """RUG sentinel: LP-tamper / liquidity-drain auto-dump. Fires on
    reserve drain or violent price collapse — both measured, never vibes."""
    if (upd.lp_reserve_usd is not None and upd.lp_reserve_at_entry_usd
            and upd.lp_reserve_at_entry_usd > 0):
        drain = (1.0 - upd.lp_reserve_usd / upd.lp_reserve_at_entry_usd) * 100.0
        if drain >= RUG_LP_DRAIN_PCT:
            return True, f"RUG: LP drained {drain:.0f}% since entry"
    if upd.minutes_since_last > 0 and pos.peak_price > 0:
        velocity = ((pos.peak_price - upd.price) / pos.peak_price * 100.0
                    / upd.minutes_since_last)
        if velocity >= RUG_PRICE_VELOCITY_PCT_PER_MIN and upd.price < pos.peak_price:
            return True, f"RUG: collapsing {velocity:.0f}%/min off peak"
    return False, ""


def evaluate_exit(pos: PaperPosition, upd: MarketUpdate) -> tuple[str, str]:
    """Deterministic pivot exits (rule-based, not learned, not trailing
    stops): rug -> time box -> peak drawdown (ratcheted in profit) ->
    dead tape -> tax trap. Returns (decision, reason); decision is 'hold'
    or 'exit'."""
    rugged, rug_reason = check_rug(pos, upd)
    if rugged:
        return "exit", rug_reason
    if upd.ts - pos.entry_ts >= MAX_HOLD_S:
        return "exit", f"time box expired ({MAX_HOLD_S / 86400:.0f}d max dwell)"
    if pos.peak_price > 0:
        drop = (pos.peak_price - upd.price) / pos.peak_price * 100.0
        drop_line = EXIT_PRICE_DROP_PCT
        if pos.entry_price > 0:
            peak_gain = (pos.peak_price - pos.entry_price) / pos.entry_price * 100.0
            if peak_gain >= PROFIT_RATCHET_TRIGGER_GAIN_PCT:
                drop_line = PROFIT_RATCHET_TIGHTENED_DROP_PCT
        if drop >= drop_line:
            return "exit", f"pivot: -{drop:.0f}% off peak (>{drop_line:.0f}%)"
    if (upd.volume_24h_usd is not None and pos.entry_window_volume_usd > 0
            and upd.volume_24h_usd < pos.entry_window_volume_usd * EXIT_VOLUME_COLLAPSE_RATIO):
        return "exit", "pivot: volume collapsed (dead tape)"
    if upd.sell_tax_pct_now is not None and upd.sell_tax_pct_now > MAX_SELL_TAX_PCT:
        return "exit", f"pivot: sell tax raised to {upd.sell_tax_pct_now:.1f}% (trap)"
    return "hold", ""


def clamp_size(proposed_usd: float, spent_today_usd: float) -> float:
    """Post-model second cap: per-token AND per-day bounds, fail-closed on
    garbage input. Returns the allowed size (0 = no trade)."""
    if (isinstance(proposed_usd, bool) or not isinstance(proposed_usd, (int, float))
            or isinstance(spent_today_usd, bool)
            or not isinstance(spent_today_usd, (int, float))):
        return 0.0
    if not (math.isfinite(float(proposed_usd)) and math.isfinite(float(spent_today_usd))):
        return 0.0
    if proposed_usd <= 0 or spent_today_usd < 0:
        return 0.0
    room = PER_DAY_CAP_USD - spent_today_usd
    if room <= 0:
        return 0.0
    return min(float(proposed_usd), PER_TOKEN_CAP_USD, room)


# Adaptive sizing (rule: crazier/thinner market -> smaller size). Proposed
# size ahead of clamp_size's hard caps. AMM price impact ~= size/liquidity
# (doc 4.2), so the entry is bounded to a negligible fraction of pool
# liquidity; the volume term keeps our size an insignificant share of daily
# flow (we must never BE the market). Below MIN_ENTRY_USD the measured
# 5-15% fee drag makes expectancy hopeless -> 0 (skip, fail-closed).
MAX_LIQ_IMPACT_FRAC = 0.001  # entry <= 0.1% of pool liquidity
MAX_VOL_FRAC = 0.01  # entry <= 1% of 24h volume
MIN_ENTRY_USD = 5.0  # dust line; below it, fees eat the trade


def adaptive_entry_size(base_usd: float, liquidity_usd,
                        volume_24h_usd) -> float:
    """Scale the base entry to conditions. Garbage/None inputs -> 0."""
    for v in (base_usd, liquidity_usd, volume_24h_usd):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return 0.0
        if not math.isfinite(float(v)):
            return 0.0
    if base_usd <= 0 or liquidity_usd <= 0 or volume_24h_usd <= 0:
        return 0.0
    sized = min(float(base_usd), float(liquidity_usd) * MAX_LIQ_IMPACT_FRAC,
                float(volume_24h_usd) * MAX_VOL_FRAC)
    return sized if sized >= MIN_ENTRY_USD else 0.0


@dataclass
class PaperBlotter:
    """Would-be trade ledger: entries/exits with cost-realistic netting
    (AMM slippage proxy + gas per round trip). Deterministic under seed."""

    seed: int = 42
    records: list = field(default_factory=list)
    spent_today_usd: float = 0.0

    def record_entry(self, mint: str, size_usd: float, price: float, ts: float) -> dict:
        allowed = clamp_size(size_usd, self.spent_today_usd)
        rec = {"side": "entry", "mint": mint, "size_usd": allowed,
               "price": price, "ts": ts,
               "rejected": allowed < size_usd}
        self.records.append(rec)
        self.spent_today_usd += allowed
        return rec

    def record_exit(self, pos: PaperPosition, price: float, ts: float,
                    reason: str, slippage_bps: float | None = None) -> dict:
        """Net a would-be round trip: exit proceeds minus entry, minus
        slippage both sides and gas. A 100% loss (rug to zero) is the
        per-position base case the math already survives. Pass slippage_bps
        for venue-aware costing (meme_venue_costs); the flat proxy holds
        when omitted so old callers are unchanged."""
        gross = pos.size_usd * (price / pos.entry_price - 1.0) if pos.entry_price > 0 else -pos.size_usd
        rate = PAPER_SLIPPAGE_PCT if slippage_bps is None else slippage_bps
        costs = pos.size_usd * rate / 100.0 * 2 + PAPER_GAS_USD
        rec = {"side": "exit", "mint": pos.mint, "size_usd": pos.size_usd,
               "price": price, "ts": ts, "reason": reason,
               "gross_usd": round(gross, 4), "costs_usd": round(costs, 4),
               "net_usd": round(gross - costs, 4)}
        self.records.append(rec)
        return rec

    def net_pnl(self) -> float:
        return round(sum(r.get("net_usd", 0.0) for r in self.records), 4)


def simulate_deterministic(seed: int = 42) -> random.Random:
    """Seeded RNG for every stochastic part of the satellite (zostaff
    discipline: same seed, same run, same artifact hash)."""
    return random.Random(seed)
