# G1 Window #4 Postmortem + Restart #6 Implementation Plan

**Source:** Full blotter analysis of `/opt/tars/data/meme_satellite/blotter.jsonl`
(127 entries / 127 exits / 24,309 screens / 428 entry blocks, 2026-10-23 09:09 UTC → 2026-11-01 00:50 UTC, 8.65 days)

**Status:** G1 FAILED on R1 (rug rate 7.17/day vs <4.6; rug avg −$12.77 vs ≥−$10).
Pivot side met reference behavior (+$13.39/trip avg) but cannot compensate under gate definition (both halves required).

---

## 1. Core finding — rug/pivot asymmetry

| Class | Count | Net | Avg | Win rate | Median hold |
|---|---|---|---|---|---|
| PIVOT | 67 | +$856.38 | +$12.78 | 73.1% | 39.4 min |
| RUG | 60 | −$730.89 | −$12.18 | 8.3% | 10.3 min |
| Total | 127 | **+$125.49** | +$0.99 | 42.5% | — |

The entire net profit comes from pivots. Rug class loses −$12.18 per trip with only 8.3% win rate. R1 fails because rug losses swamp pivot gains.

**Exit layer works:** rugs are caught fast (10.3 min median). The problem is loss magnitude per rug, not detection speed.

---

## 2. Time-of-day findings (full data, not partial analysis)

The old GOLD/DEATH windows came from a 102-trade partial analysis and do not match the full 127-entry blotter:

| Window | Entries | Net | Win rate |
|---|---|---|---|
| NEUTRAL (everything else) | 25 | **+$107.03** | **53.6%** |
| GOLD (old: 02–07, 16–19) | 50 | +$16.16 | 48.0% |
| DEATH (old: 00–02, 07–11, 12–13, 20–24) | 52 | +$2.31 | 30.6% |

Hour-level standouts:
- Hours 0–1: **0% win rate**, −$73.62 combined (clearly DEATH)
- Hours 20–23: consistently negative (clearly DEATH)
- Hour 4: +$52.07, 50% win, n=6
- Hour 10: +$165.27, 42.9% win, n=7
- Hour 18: +$97.62, 66.7% win, n=6
- Hour 19 (cited as "0% rug" in partial analysis): actually −$3.83 net, 3 rugs, n=6

**New windows deployed to poller** (`src/meme_time_gate.py`, verified md5 match on VPS):
- DEATH: `(0, 2)` and `(20, 24)`
- GOLD: `(4, 7)`, `(10, 12)`, `(18, 20)`
- NEUTRAL: everything else

**Caveat:** Sample sizes per hour are small (n=2 to n=9). These windows are a hypothesis to validate, not a finished system. Per Grok's analysis of memecoin time edges, re-test every 4–8 weeks or after any regime change. Aim for 30–50 entries per bucket before treating it as a hard rule.

---

## 3. Creator serialization — positive pre-entry signal

| Creator type | Count | Net | Avg | Win rate |
|---|---|---|---|---|
| Creator-reused (>1 entry) | 41 | +$63.96 | **+$1.56** | **43.9%** |
| One-off creator | 86 | −$111.92 | −$1.30 | 31.4% |

20 creators launched multiple entered mints; top creator had 3 entries. This contradicts the serial-rugger narrative — repeat deployers perform better, not worse.

RugCheck provides no signal: 123/127 entered mints had `rugcheck_rugged=False`; zero entered mints were flagged rugged, yet 60/127 exits were rug-class losses.

---

## 4. Liquidity floor and entry price — inverse correlation

| Liquidity bucket | Count | Net | Avg | Win rate |
|---|---|---|---|---|
| ≤$50k | 102 | **+$99.39** | **+$0.97** | **38.2%** |
| $50k–$100k | 21 | −$107.59 | −$5.12 | 28.6% |
| $100k–$200k | 4 | −$39.77 | −$9.94 | 0.0% |

Entry price:
- $0.0001–$0.0005: **+$98.38** net (only profitable band)
- Above $0.0005: all losses
- ≤$0.0001: −$103.81 net

**Higher liquidity = worse performance.** The liquidity gate is currently too high and admits the wrong tokens.

---

## 5. Implementation roadmap (one change per window)

### Window A — Entry filter changes (priority)

**Files:** `src/meme_satellite.py`, `src/meme_live_trader.py`, `src/meme_exit.py`

1. **Lower `MIN_LIQUIDITY_USD`** to target ≤$50k band. Add `ENTRY_PRICE_MIN_USD=0.0001` and `ENTRY_PRICE_MAX_USD=0.0005` filters in `scream_filter`.
2. **Extend `deployer_history.json`** to track per-creator: launch count, cumulative PnL, win rate, last launch timestamp.
3. **Add creator serialization signal** to `_screen_mint()` in `src/meme_live_trader.py`: look up `deployer_history[creator_address]`, add `creator_launch_count` and `creator_avg_pnl` to snapshot. `scream_filter` adds Gate: prefer or require `creator_launch_count >= 2` (or skip one-off creators with high `creator_hold_pct`).
4. **Drop RugCheck `rugcheck_rugged` as a hard gate.** It has zero predictive power on entered mints. Keep note-only or remove to save rate limits.

### Window B — Exit optimization for rug class

**File:** `src/meme_exit.py`

5. **Tighter initial stop / shorter max hold** for rug-class entries. Current −25% peak drawdown stop allows −$12.18 avg rug loss. Consider −15% off peak or 15-min time-box in the first 20 minutes of a position.
6. **Size reduction** on entries where `creator_launch_count == 1` (higher rug probability).

### Window C — Time gate recalibration

**File:** `src/meme_time_gate.py`

7. **Track per-window entry count and PnL** in a rolling log. Re-test windows every 4–8 weeks or after regime change (volume collapse, new launchpad, sustained drop in runner size). Keep rolling 90–180 day window alongside full history.
8. Adjust DEATH/GOLD boundaries only when a bucket reaches 30–50 entries and shows statistically distinct expectancy.

---

## 6. Data collection requirements

| Target | Trades | At ~14.7 entries/day |
|---|---|---|
| First rough impression | 100 | Already have |
| Metrics stabilize | 300 | ~12 more days |
| Serious sample | 500 | ~25 more days |
| Live-deployment grade | 500–1,000 | ~25–60 more days |

**Per-bucket minimum:** 30–50 entries before treating any time window as a hard rule. Several buckets currently have single-digit counts (hour 0: n=2, hour 7: n=1).

## Market regime context (added 2026-11-01)

**Current regime (as of early Nov 2026):**

| Layer | Regime | Evidence |
|---|---|---|
| Memecoins | Post-mania attrition | Market cap ~$29–31B vs ~$150B peak (Dec 2024, −80%); daily launches still high (30k–50k) but quality poor; graduation/survival ~2% or less; 79%+ die after day 1 |
| Broader crypto | Late bear / early accumulation-recovery | BTC ~35–40% off Oct 2025 peak (~$124k–$126k); recovered from June 2026 low (~$57k–$59k); shallowest major bear on record (−53–54% max DD vs 77–85% historical) |

**Implications:**

- **Time gates are regime-stamped.** The GOLD/DEATH windows deployed to the poller were calibrated in attrition. Grok's analysis: time-of-day edges that worked in frenzy weaken or invert when organic flow dries up. Do not treat 04:00–07:00 / 10:00–12:00 / 18:00–20:00 as universal truth — they are an attrition hypothesis.
- **Creator serialization signal (+$1.56 avg)** was measured in attrition (thin buyer quality). In mania, one-off creators may perform better (retail FOMO); reused creators may dominate even harder (KOL repeat launches). Do not assume attrition calibration holds if regime flips.
- **Expect lower absolute profitability and more false signals** than in mania. The poller's job now is data collection + death-window skipping, not printing money.

**Regime tagging requirement:** Every window archive (e.g., `blotter.window5.jsonl`) must be tagged with:
1. Memecoin regime (attrition / mania / recovery)
2. Broad market regime (bear / accumulation / recovery)
3. BTC drawdown % from peak
4. Memecoin market cap tier

**Re-test cadence:** Re-validate time gates every 4–8 weeks or after any obvious regime change (volume collapse, new dominant launchpad, sustained drop in average runner size, memecoin market cap recovery toward prior highs). Keep a rolling 90–180 day window alongside full history so you can see when old gold windows stop working.

**Decision rule remains:** Restart #6 is not authorized as a live flip. Paper-only continues until a new pre-entry signal class clears R1 on paper.

---

## 7. Files affected

| File | Role | Change |
|---|---|---|
| `src/meme_time_gate.py` | Time gate windows | Updated to data-driven windows (deployed, md5-verified on VPS) |
| `src/meme_satellite.py` | Scream filter + constants | Lower liquidity floor, add entry price band |
| `src/meme_live_trader.py` | Screening pipeline | Add creator serialization to snapshot; pre-entry gate |
| `src/meme_exit.py` | Exit engine | Tighter stop / shorter hold for rug class; size reduction |
| `deployer_history.json` | Creator tracking | Extend to per-creator PnL + win rate + launch count |

---

## 8. Decision rule at day 7

Per pre-registered invalidation rule (from window #5 pre-registration):
- All four criteria hold → keep gate armed; next single change carries forward.
- R1 fails (rug rate ≥ 4.6/day or rug avg < −$10) → revisit only with a different pre-entry signal class; creator serialization is the candidate here.
- R2 fails (gate killing winners) → lower threshold or drop stage.
- Void → fix discovery sampling first; do not re-arm.

**Restart #6 is not authorized as a live flip.** Paper-only continues under window rules until a new pre-entry signal class clears R1 on paper.
