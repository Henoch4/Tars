# LIVE TRADING PLAYBOOK — profit locking, circuit breakers, capital extraction

STATUS: **DESIGN ONLY for ENABLING. The post-G1 scaffolding is PREPARED**
(offline, unwired, default-OFF) so the eventual flip is a flag, not a build.
Nothing here is enabled: ALLOW_LIVE stays false, DRY_RUN stays explicit.

==============================================================================
THE GATE — the one and only build gate for this file
==============================================================================
G1 = >=7 days of continuous paper operation AND >=30 net-positive paper
round-trips (net-positive after fees, under the CURRENT rules).

That is THE only gate. Nothing in this file is built, wired, or enabled until
G1 is met:
  - ALLOW_LIVE stays false; DRY_RUN stays explicit.
  - No live entry, no live exit, no capital moved.
  - Do not dilute G1 to ship sooner. Do not invent shadow gates.

Everything else — candidate filters, notes-only fields, open policy questions
(Q2/Q4/Q5/Q7), cosmetic fixes — is NOT a gate and NOT a blocker. Analysis and
queuing are always fine; only BUILDING or ENABLING is frozen until G1. When G1
clears, execute section 6 ONE structural piece at a time, each with a
regression test, verified with the same compile/test/ship discipline as the
cooldown, before raising the risk budget.

Why this file exists: automated systems that survive memecoin trading treat
realized profit as something to LOCK and REMOVE from the risk surface, not to
leave in the hot trading wallet. The dominant production pattern is
mechanical exits + hard circuit breakers + deliberate capital extraction —
never discretion. This is captured here so the live phase has a written target.

--------------------------------------------------------------------------------
0. THE OPERATING PRINCIPLE
--------------------------------------------------------------------------------
- Realized gains are defended immediately (partials + trailing + break-even).
- Already-realized equity is protected from the NEXT trade (portfolio breakers).
- Accumulated profit is periodically REMOVED from the trading wallet.
- The goal is an equity curve that is not destroyed by the next rug cluster,
  not maximum theoretical return.

--------------------------------------------------------------------------------
1. PER-POSITION PROFIT LOCKING (mechanical, on every position)
--------------------------------------------------------------------------------
  a. TAKE-PROFIT LADDER (partial sells):
     - Sell enough at ~2x to recover the original stake ("house money").
     - Tranches at 3-5x / 8-10x.
     - Leave a small "moonbag" on a trailing stop.
     Turns the remaining position into pure-profit exposure (zero original
     capital at risk).
  b. TRAILING STOP / BREAK-EVEN STOP:
     - Trail the high-water mark (common: 15-35% off peak, tighter early,
       looser after large gains).
     - After a profit threshold, move the stop to entry so a winner cannot
       become a loser.
  c. TIME-BOX EXIT: force-close after a fixed window (minutes-hours on
     curve tokens; up to 24h-7d on graduated ones). Stalled positions are
     closed so capital is not trapped.
  d. RUG SENTINEL: LP-drain / velocity auto-dump (already exists).

  REPO MAPPING:
     - EXISTS  : peak-drawdown exit (EXIT_PRICE_DROP_PCT, satellite) — a crude
                 trailing stop; time-box (MAX_HOLD_S=7d); rug sentinel
                 (check_rug: LP drain + price velocity).
     - MISSING : TP ladder (partial sells) — exits are currently all-or-nothing.
     - MISSING : break-even stop (stop at entry after a profit threshold).
     - NOTE    : at the $25 adaptive size, a 3-5 tranche ladder is nearly dust
                 ($5 min viable entry). Ladder sizing needs a larger base or a
                 min-tranche floor — a real design question, not a one-liner.

--------------------------------------------------------------------------------
2. PORTFOLIO-LEVEL CIRCUIT BREAKERS (protect already-realized equity)
--------------------------------------------------------------------------------
  a. DAILY LOSS / DRAWDOWN LIMIT (typical 3-8% of equity or a fixed amount)
     -> pause new entries until next day / manual review.
  b. CONSECUTIVE-LOSS STREAK (e.g. 5-6 losses) -> cooldown period.
  c. LIFETIME / PEAK-TO-TROUGH DRAWDOWN (e.g. 25-40%) -> emergency stop,
     often force-selling remaining positions.
  d. MAX OPEN POSITIONS + MAX PORTFOLIO HEAT (total capital at risk across
     all positions kept under ~10-25%).

  REPO MAPPING:
     - EXISTS (EVM book) : src/execution/risk_gate.py — kill switch,
                           reduce-only, slippage collar, daily-loss auto-trip;
                           MAX_DAILY_LOSS_USD, DRAWDOWN_WINDOW_DAYS,
                           DRAWDOWN_LOSS_MULT (settings/env).
     - EXISTS (satellite) : session spend cap (max_session_usd=$100) + the
                           HALT file (blocks new entries, exits keep running).
     - MISSING (satellite): daily-loss cap, loss-streak cooldown, max-open /
                           total-heat caps, and any AUTO-trip on drawdown.
                           Today the satellite halt is manual only.

--------------------------------------------------------------------------------
3. CAPITAL EXTRACTION AND WALLET HYGIENE (the actual "saving" step)
--------------------------------------------------------------------------------
  a. Dedicated HOT wallet only, funded with just what the bot may risk.
     Never the main holdings.
  b. Periodic (daily/weekly, or after a significant positive day) transfer of
     net realized gains OUT of the hot wallet to a cold/storage wallet.
  c. Optional on-the-fly split: a configurable % stays for compounding, the
     rest goes to treasury/owner on each realized profit event.
  d. Kill switch that can liquidate everything and halt immediately.
  e. Security: API keys have NO withdrawal permission; hot-wallet key isolated;
     balances deliberately small.

  REPO MAPPING:
     - EXISTS : /etc/tars/book2-satellite.json (the Book-2 hot keypair, 64-int
                JSON; path-only travel, never contents). solders present on VPS.
     - EXISTS : HALT file kill switch (satellite), risk-gate kill switch (EVM).
     - MISSING: sweep schedule / treasury split / cold-wallet destination;
                funding policy for the hot wallet; withdrawal-permission audit
                of any exchange API keys.
     - NOTE   : while paper-only there is NO wallet balance — the paper
                "+$13.05 realized" is a bookkeeping number, not sweepable funds.

--------------------------------------------------------------------------------
4. SUPPORTING RULES THAT MAKE THE ABOVE EFFECTIVE
--------------------------------------------------------------------------------
  - Strict position sizing (0.1-2% of equity, or a fixed small SOL amount).
    EXISTS: adaptive_entry_size + clamp_size + per-token/per-day caps.
  - No re-entry on recently exited / rugged mints (cooldown after exit).
    EXISTS: SHIPPED 2026-10-18 (REENTRY_COOLDOWN_S / RUG_REENTRY_COOLDOWN_S).
  - Pre-trade safety filters so fewer positions reach the exit stage needing
    protection. EXISTS: scream_filter (rugcheck, holders, concentration,
    creator history, taxes, liquidity, mcap, round-trip cost).
  - Logging of every exit reason + PnL so expectancy can be measured.
    EXISTS: JSONL blotter (screen/entry/exit/entry_blocked records).

--------------------------------------------------------------------------------
5. WHAT ALREADY EXISTS IN CODE (do not rebuild)
--------------------------------------------------------------------------------
  - src/meme_execute.py : full guarded LIVE path — quote_swap -> sign_order_tx
    (solders) -> execute_signed (POST /swap/v2/execute) -> ExecutionLedger.
    Cost-hurdle (MAX_COST_FRACTION_OF_SIZE=10% at $5 scale), session cap
    (MAX_SESSION_SPEND_USD=$5). live_buy() refuses to broadcast unless a
    non-empty `confirm` string is passed (two deliberate acts).
  - scripts/meme_live_buy.py : operator-driven one-shot live BUY
    (--mint --usd [--live --confirm "..."]), WSOL input, Solscan link.
  - ALLOW_LIVE interlock (settings): ALLOW_LIVE=true is only meaningful beside
    an explicit DRY_RUN (fails closed otherwise).
  - Satellite loop is PAPER-ONLY: try_enter records paper=True; the executor is
    "called only when explicitly wired in with a confirm."
  - PREPARED (built, tested, NOT wired — default-OFF so G1 is untouched):
      - src/meme_g1.py + scripts/meme_g1_report.py : G1 progress report (read-only
        gate gauge). CLI: `venv/bin/python scripts/meme_g1_report.py [--json]`.
      - src/satellite_breakers.py : Phase-2 breakers (daily-loss, streak, max-open,
        heat, drawdown halt) as a PURE class. Ported from risk_gate.py.
      - src/meme_execute.py::live_sell : Phase-1 sell path (confirm-gated, and it
        deliberately BYPASSES ExecutionLedger.approve() — an exit frees capital,
        it must never be blocked by a buy-side entry cap).
      - src/meme_reconcile.py : Phase-3 local-vs-chain reconciliation (pure).
      - src/meme_shadow.py : offline shadow-filter replay (candidate-filter testing).
      - src/meme_loop.py : SatelliteLoop now accepts breakers=/on_entry=/on_exit=
        (all default None). At None the loop is byte-for-byte the G1 paper loop;
        wiring tests pin that default-OFF is inert. Halt transitions now dispatch
        a best-effort alert via src/alerting.send_alert (no-op unless
        ALERT_WEBHOOK_URL is set — so it cannot perturb the paper measurement).

--------------------------------------------------------------------------------
6. PHASED EXECUTION PLAN (post-G1) — AUTHORITATIVE ORDER
--------------------------------------------------------------------------------
RATIONALE (what surviving production systems do): make sure you can CLOSE and
STOP before you ever OPEN with real size. Value order = close path, then
breakers/alerting, then truth (persistence/reconciliation), then edge upgrades,
then capital. Execute ONE structural piece at a time, re-verified with the same
compile/test/ship discipline as the cooldown, and only then raise the risk budget.

--- PHASE 0 — DOCTRINE GATE (already correct; non-negotiable) --------------
  0a. G1: >=7 days AND >=30 net-positive paper round-trips. (Now 9/30, day 1.)
  0b. Flip ALLOW_LIVE=true + restart (explicit second act); DRY_RUN explicit.
  Do not dilute 0a to go live sooner.

--- PHASE 1 — LIVE EXIT PATH (highest structural priority) -----------------
  Neither the loop nor any script can currently CLOSE a live position. Fix first.
  CODE REALITY: quote_swap(input_mint, output_mint, amount_raw, taker, slip) in
  src/meme_execute.py is ALREADY DIRECTION-AGNOSTIC, and sign_order_tx /
  execute_signed are generic. A sell is therefore:
      quote_swap(meme_mint, SOL_MINT, position_token_balance_raw, taker)
  i.e. live_buy() with swapped mints and the position's real token balance.
  Steps:
  1a. Add live_sell()/execute_exit() in meme_execute.py reusing quote/sign/execute
      with sell-side params + a read of the wallet's actual token balance.
  1b. CRITICAL: sells must BYPASS ExecutionLedger.approve() — that cap is a
      BUY-side entry/session-spend cap; an exit FREES capital and must never be
      blocked by an entry cap. Give exits their own (uncapped) path.
  1c. Mirror the paper check_exits triggers (pivot, rug, time, trailing, TP) so a
      live position closes on the same signals the paper one would.
  1d. Keep the fail-closed confirm interlock: a live sell fires only with the
      same deliberate confirm that guards a live buy.
  TEST SEQUENCE (still G1-gated): dry-run sell a dust position on-chain -> one
  full round-trip on the smallest budget -> assert the sell lands, the local
  position closes, and realized PnL is recorded.
  Until 1a-1d exist, a live open is an uncontrolled liability.

--- PHASE 2 — SATELLITE CIRCUIT BREAKERS + ALERTING (pure safety) -----------
  PORT, don't build. src/execution/risk_gate.py ALREADY has: max_daily_loss_usd,
  DRAWDOWN_BREACH + drawdown_loss_mult, a trailing loss STREAK (streak_days), and
  a kill switch. src/alerting.py ALREADY has send_alert(event, severity, detail)
  with per-event cooldowns. The satellite has only a $100 session cap + manual
  HALT. Bring the proven gates over:
  2a. Daily-loss / drawdown cap (3-8% or fixed) -> auto-pause new entries.
  2b. Consecutive-loss streak cooldown.
  2c. Max open positions + portfolio heat cap.
  2d. Keep the $100 session cap + HALT as LAYERS, not the only layer.
  2e. Set ALERT_WEBHOOK_URL (or Telegram) BEFORE live capital; alert on circuit
      trip, failed live tx, a position that cannot be sold, reconciliation
      mismatch. Silent failure is the most common operational killer.
  2f. File-based kill switch (HALT/KILL_SWITCH) -> stop + optionally force-exit.
  These do not change strategy edge; implement before any live size.

--- PHASE 3 — PERSISTENT / RECONCILED LIVE FILLS (chain = source of truth) --
  In-memory ExecutionLedger is fine for paper; not for live.
  3a. Persist every live fill (signature, mint, side, amount, price, ts) to
      JSONL/SQLite.
  3b. On restart / periodic tick: query wallet token balances + recent
      signatures (book2_pubkey + the RPC pattern already used in ops) and
      RECONCILE against local open positions. Mismatch -> alert + pause new
      entries until resolved.
  3c. Record intended AND actual fill (slippage, fees) so expectancy stays honest.

--- PHASE 4 — PROFIT-LOCKING UPGRADES (TP ladder + break-even) --------------
  All-or-nothing exits turn winners into losers. Implement AFTER the live exit
  path exists (Phase 1), driven by the same price snapshots the paper exits use.
  4a. Partial ladder: e.g. 25-50% at 1.5-2x to recover cost ("house money");
      further tranches at higher multiples.
  4b. Move stop to break-even / small profit after the first tranche.
  4c. Trailing stop on the remainder.
  4d. Keep pivot/rug/time exits as the hard floor.
  CAVEAT: at the $5-25 base, a multi-tranche ladder hits the $5 min-tranche
  floor — ladder sizing needs a larger base or an explicit min-tranche rule.

--- PHASE 5 — CAPITAL AND OPERATIONAL HYGIENE ------------------------------
  5a. Define a hard risk budget (e.g. 0.5-2 SOL total hot balance) and fund ONLY
      that. (Current Book-2 wallet holds dust: 0.0388 SOL + 0.55 USDC — a test
      float, not a risk budget.)
  5b. Sweep realized profit above a floor out of the hot wallet. PRIMITIVE
      ALREADY EXISTS: src/wallet.py has ColdTier, sweep_to_cold(profit_usd,
      hot_signer_id) and confirm_sweep — reuse it; add a schedule/cold dest.
  5c. Audit that no exchange/API key the bot can touch has withdrawal permission.
  5d. Explicit max position size and total hot-wallet exposure.

--- SEQUENCING (do in this order) ------------------------------------------
  1. Live sell path + dust test.          4. TP ladder / break-even.
  2. Circuit breakers + alerting.          5. Capital funding + sweep policy.
  3. Persistent fills + reconciliation.

--- CANDIDATE FILTERS (queued; NOT a gate, NOT a blocker) ------------------
  Source: the 2026-10-18 @StarPlatinum_ "manufactured demand" thread + the
  Bitquery data it cites (21% of 8.2M launches dev-bundled; 65% of devs sell in
  the first minute; 58% of Solana DEX volume non-genuine). Threat model =
  bundled launch supply (one entity across many wallets) + wash volume + early
  dev distribution. Where our filter stands TODAY:
    - Caught, PARTIAL: dev's SINGLE wallet > 10% (Gate 3b). meme_rugcheck.py:82
      matches the holder whose address == creator, so a bundle SPREAD across
      wallets is invisible to it.
    - Gap: top-10 <= 50% passes even if ~40% is one bundler (MAX_TOP10_HOLD_PCT).
    - Gap: no live "dev distributing NOW" check (creator_past_rugs is history).
    - Gap but SIGNAL ALREADY WIRED: Jupiter organicScore / organicScoreLabel
      (meme_jupiter.py:168) is note-only (meme_satellite.py:250). Promoting it
      to a real or soft gate is the cheapest high-value candidate.
    - Gap: no funder-clustering of top holders (same-SOL-source wallets).
  Analysis/queuing free; building frozen until G1.

--- NOT BLOCKING (queued cosmetic / data) ----------------------------------
  - symbol="" in meme_loop.try_enter — FIXED 2026-10-19: TokenSnapshot.symbol is
    now plumbed (enrich_mint -> snapshot_for_mint -> _enrich -> try_enter) and
    shown in portfolio(); no gate impact.
  - Blur bandwidth top-up (discovery on the DexScreener fallback meanwhile).
  - Open policy/data items: Gate 5 floor (Q4), Gate 3b absent (Q2), Gate 1
    graduation source (Q7), concentration spot-check (Q5).

--------------------------------------------------------------------------------
7. GUARDRAIL
--------------------------------------------------------------------------------
One change / one gate at a time, always with a regression test, always measured
against the blotter before the next change. Nothing in this file is authorised
to be built while the satellite is paper-only and G1 is unmet.

--------------------------------------------------------------------------------
8. GO-LIVE RUNBOOK (satellite) — execute ONLY when G1 is met
--------------------------------------------------------------------------------
PRECONDITIONS (all true, in this order — do not skip to the flip):
  1. G1 met: `venv/bin/python scripts/meme_g1_report.py` prints "G1: PASS".
  2. Alerting live: ALERT_WEBHOOK_URL is set in /etc/tars/agent.env (Phase 2e).
     Silent failure is the #1 operational killer — no alerts, no live capital.
  3. Breakers wired (Phase 2): the loop is constructed with breakers=... . Until
     then live has only the $100 session cap + manual HALT.
  4. Live EXIT wired (Phase 1): live_sell is reachable from check_exits. Until
     then a live open is an uncontrolled liability you cannot close.
  5. Reconciliation wired + alerting on mismatch (Phase 3).
  6. Hot wallet funded to the HARD risk budget only (Phase 5a) — never the main bag.

FLIP (the satellite only; ONE structural change at a time):
  A. Rollback anchor:
       ssh root@<vps> "cp /etc/tars/agent.env /etc/tars/agent.env.pre-live.$(date +%s) && md5sum /etc/tars/agent.env"
  B. Edit /etc/tars/agent.env:
       ALLOW_LIVE=true
       DRY_RUN=false
     (ALLOW_LIVE=true without an explicit DRY_RUN fails closed per settings.allow_live.)
  C. Restart ONLY the satellite:
       systemctl restart tars-satellite && systemctl status tars-satellite --no-pager
  D. Verify the first tick, then the next two:
       journalctl -u tars-satellite -n 50 --no-pager
       venv/bin/python scripts/meme_g1_report.py          # still reports off the blotter
       # blotter: new fills should show paper=false on the live path
  E. First live round-trip on the SMALLEST budget. Confirm ALL of: the sell lands,
     the local position closes, realized PnL is recorded, and an alert fired.
     Do NOT raise size until one full clean round-trip is proven.

ROLLBACK (any time):
  - Stop NEW entries immediately (exits keep running — deliberate):
       venv/bin/python scripts/meme_runner.py --halt
  - Full revert:
       ssh root@<vps> "cp /etc/tars/agent.env.pre-live.<ts> /etc/tars/agent.env && systemctl restart tars-satellite"
  - Live sell/buy are confirm-gated: nothing broadcasts without the explicit confirm.

NOTES:
  - Breakers/hooks are CONSTRUCTOR params today. Enabling them live is a code
    wiring step in scripts/meme_runner.py (Phase 2), not an env flag. Do that as
    its own change, before the flip, with a regression test.
  - Paper and live share the loop; a live entry records paper=false via the executor.

================================================================================
9. DATA-SOURCE REPAIR 2026-10-19 (G1 clock restarted)
================================================================================
WHAT HAPPENED: RugCheck's domain went NXDOMAIN (last data 2026-10-18 23:25Z,
verified DNS-dead on Cloudflare/Google/Quad9). It was the primary supplier of
creator_address + creator_hold_pct, so Gate 3b (creator share) became
unmeasurable: 3,854 of 4,585 graduated screens died on missing_data. Separately
Jupiter's keyless /tokens/v2/search 429s after ~5 calls (measured 2026-10-19).

THE REPAIR (data-source only; NO gate threshold changed):
  - creator_address  <- Jupiter /tokens/v2/search `dev` field (deployer), for
    GRADUATES ONLY (that is where Gate 3b reads it; non-graduates die at Gate 1
    and would just burn the rate-limit budget).
  - creator_hold_pct <- computed ON-CHAIN from Helius:
    getTokenAccountsByOwner(dev, {mint}) sums / mint supply * 100. Chain truth,
    deterministic, free. Multiple accounts summed; Token-2022 layout covered
    (amount u64 at offset 64); transport failure stays None (SKIP-default, never
    a false 0).
  - creator_launches <- Jupiter `dev_mints` (serial-deployer count), NOTE ONLY.
    Live values run hot: 1 / 4 / 144 / 893 / 6148 / 14067 / 22127 launches.
    Promoting it to a gate needs a measured distribution first (one change at a
    time).
  - token_info retries a transient 429 up to 3x with 1s sleep; anything else
    fails fast to {} (unit-tested, no real sleeps in tests).
  Deployed via md5-verified scp; backup at /opt/tars/backup-datasource-fix/.

G1 CLOCK: RESTARTED (2026-10-19). The former sample (10 round-trips, net
+$9.10) was earned partially blind to creator share and is archived, not
decision-grade: /opt/tars/data/meme_satellite/blotter.pre-datasource-fix-2026-10-19.jsonl
(+ positions.pre-...json), and the brief intermediate window
(_: blotter.fix-v1-2026-10-19-v2.jsonl). Fresh window marker:
G1_CLOCK_RESTART.json sits in the data dir. deployer_history.json reset with the
window (it compounds from the blotter; it will rebuild under the new regime).

OPERATOR DISCIPLINE for this window:
  - NEVER run bulk _enrich loops manually (a 150-mint probe 429'd Jupiter
    AND Helius for minutes and starved real cycles). Diagnose from the blotter
    (scripts/meme_runner.py --diagnose) and single-mint reads only.
  - First verified post-repair graduated screen (cycle 1, 2026-10-19): mint
    41mKZPc... rejected thin_liquidity at pool $10 — creator cluster 0.0%
    (computed), launch count 1 noted, round-trip 3.2% sellable. The
    missing_data wall did not fire.

OPEN: Q4 unchanged — MIN_LIQUIDITY_USD 20k vs $2-10k typical post-graduation
pools; if the window starves on thin_liquidity, that threshold review is the
NEXT one-change candidate, not a mid-window tweak.

================================================================================
10. QUEUED CHANGE CANDIDATES (post-window ONLY — operator directive 2026-10-19)
================================================================================
Zero code and zero external calls while the G1 window runs. When the window
closes (or is deliberately restarted for a documented reason), act ONE change
at a time, in this order:

  #1 — GoPlus Solana token-security (gopluslabs.io/token-security/solana):
     free read-only enrichment for transfer fee / tax / mutable metadata /
     extra authority flags. Purpose: fill the still-unobserved buy_tax_pct /
     sell_tax_pct gap and strengthen Token-2022 handling. NOTE-ONLY or soft
     filter first; a hard gate only after measured post-G1 data.
     First action once allowed: a single read-only probe to confirm endpoint,
     response shape, rate limits, and which fields actually fill our gaps.
     (Source: @TheMaran free rug-check thread, X status 2112114049366523960.)

  #2 — Holder-cluster / linked-wallet tightening (inspired by InsightX
     Atlas >=15%-group rule, same thread): requires a free API, a cheap
     on-chain approximation, or manual-review tooling — still research,
     not implementation.

  #3 — Solsniffer snifscore (<70 skip) + snifwallets serial-launcher read
     (100+ tx/24h mostly-sell PnL): note-only corroboration of the
     serial-launcher signal already carried by Jupiter dev_mints. Lowest
     priority.

================================================================================
11. OPPORTUNISTIC EVENT OVERLAY (post-live only)
================================================================================
GTA 6 launch window (and similar major cultural releases): consider a small,
time-boxed allocation to related memes in the first 24-72h of the actual
release window. Does not alter Book-2 graduation-survival logic. Requires:
  - a separate, pre-defined risk budget that does not touch main Book-2 capital;
  - the full safety checklist on every coin (mint/freeze revoked, creator
    hold, top-10 concentration, liquidity, no active tax/honeypot flags);
  - an explicit exit plan (3-5 day time stop or mechanical TP ladder) so it
    cannot become an open-ended bag.
Re-evaluate only when live infrastructure is proven (gate §8 flip done,
breakers wired, capital hygiene running). Signed-off as a written reminder
that money can be made on event-driven narrative spikes if risk is tightly
boxed. (Source: @SpoiledBySpoily, X status 2111877036658544758.)

================================================================================
12. G1 RESTART LEDGER (each restart = a fresh gate; chain must stay explicit)
================================================================================
  #1 2026-10-19 06:32 CEST — first data-repair pass archived (fix-v1 cursor
     moved to the v2 repair), desktop: blotter.fix-v1-2026-10-19-v2.jsonl.
  #2 2026-10-19 11:02 CEST — repaired funnel window opened (Jupiter creator
     fields + on-chain creator_hold_pct + creator_launches note). Archive:
     blotter.pre-datasource-fix-2026-10-19.jsonl (net +$9.10, NOT
     decision-grade).
  #3 2026-10-19 ~16:45 CEST — profit ratchet, the ONE exit-rule change of
     this window (operator-approved): in evaluate_exit the trailing line
     tightens -25% -> -10% once peak gain >= +25% of entry_price. Nothing
     else touched. Rationale: the 4h window ran 5/5 stop-outs (net -$40.54)
     under trail-only, while the live doctrine (sec.0: realized gains
     defended immediately) is what we will actually trade — so paper must
     measure the ratcheted rules, and the change was made while only 4h of
     window was sunk. Evidence on-box: constants 25.0/10.0 live; a +50%
     peak position exits with "pivot: -10% off peak (>10%)". Archive:
     blotter.pre-ratchet-2026-10-19.jsonl; marker G1_CLOCK_RESTART.json.

   Status snapshot 2026-10-20 02:30 CEST (restart #3, mid-window; NO rule
   changes — measurement only):
     clock 1.53->2.22/7d; probes 23->36/30 (count criterion MET at 36);
     net -$171.72 -> -$237.90; 0 open positions; 640 cycles; 4433->6383
     screens; 66 cooldown blocks. Criteria days=no count=OK net=no ->
     G1 NOT MET. Full-ledger attribution (36 exits):
        pivot-class exits 17, net +$29.41 (avg +$1.73; 7 net-positive:
        8Bm6eG +18.68 ratchet pivot, 96AsCW +19.97 salvage-exit via the
        collapse breaker, 54UN9Y +10.37, 2qgnTg +7.46, 6gAHVh +6.16,
        3xBE38 +4.90, 2gVNeu +0.73);
       RUG-class exits 19, net -$267.32 (avg -$14.07).
     Kill pattern unchanged and quantified: 90-95% LP-drained within 5-15m
     of entry (worst: 9JUYGi -25.83, 8ek2qv -25.68, 75zGdp -25.41,
     4tieig -25.74, 4xwPPT -25.46) or 33-57%/min collapses. LP-drain
     breaker escape costs ~25% slippage -> ~full stake loss per rug.
     The exit layer held: every rug caught in 5-36m, winners harvested by
     the ratchet. Entry gate structurally blind to this class: all 19 rug
     entries passed the FULL structural screen (RugCheck clean, renounced
     mint/freeze, top-10 12-27%, creator cluster <=1%, pool $20k-72k,
     exitable round-trip cost 1.7-6.9%), ages 0.2-2.9h; 'lp_locked
     unobserved' free-passed on all 19, buy/sell tax unobserved, and
     organic score 0 (no organic rank data) informational-only on 6/11
     sampled. Probe #1 (GoPlus LP-lock % / tax fields) is therefore the
      load-bearing window-4 candidate; window verdict: not reachable
      (+$238 needed in 4.78d vs observed -$30/d).

   GoPlus Solana probe 2026-10-20 (read-only, operator-approved, NO deploy):
   endpoint /api/v1/solana/token_security (beta; the EVM path
   /api/v1/token-security/solana 404s). 12 window-3/4 mints queried
   (8 rug-class + 2 winners + 2 re-tests): 4/12 not even indexed
   (code 7012/7013), 8/12 return baseline SPL flags (mintable/freezable/
   metadata_mutable/closable/balance_mutable_authority all 0, matching
   our own renounced-authority checks) but ZERO LP surface: dex[] absent
   (no burn_percent/tvl), lp_holders empty on 10/12, holders populated on
    1/12. Lazy-indexing hypothesis tested and REJECTED (two spaced rounds
    identical). VERDICT: NEGATIVE for the load-bearing use case — it would
    have flagged ~0 of the 19 rug entries; the LP-drain pre-signal cannot
    come from GoPlus (beta coverage is thin/EVM-shaped for the pump.fun
    universe). GoPlus PARKED (raw responses archived at
    /tmp/goplus_probe/ on the VPS). Candidate #1's replacement for
   window-4 consideration: RPC LP-velocity pre-entry gate — read the
   pool's LQ vault balance at multiple screens (solana_rpc already reads
   pool vaults), reject entries whose LP is falling faster than a
     stated threshold (pre-entry analogue of the LP-drain breaker that
     currently fires too late at ~25% slippage cost).

   ---------------------------------------------------------------------------
   RESTART #4 PRE-REGISTRATION — written 2026-10-23, BEFORE the window opens
   ---------------------------------------------------------------------------
   State at registration: restart #3 clock 3.39/7d, 56 exits, net -$394.79,
   0 open, attribution rug 31 trips -$468.02 (avg -$15.10) vs pivot 25 trips
   +$73.22 (avg +$2.93). Window #3's verdict is already decided (net is
   unrecoverable in the remaining days); this block fixes restart #4's success
   criteria now, so they cannot be picked after its numbers are in.

   THE ONE CHANGE: a confirmation stage in front of paper entry. Instead of
   entering on the screen pass, the loop records the pass, waits for a second
   LP observation of the same mint (next cycle, or an explicit re-enrichment
   of the pending mint), and enters only if the pool is not visibly draining
   (src/meme_lp_velocity.liquidity_verdict, 4.0 %/min, 900 s episode window).
   Armed = the reject action; unarmed = notes only. Nothing else changes in
   the window: no filter edits, no exit-rule edits, no sizing edits, no
   cooldown edits.

   Threshold evidence (offline replay of the window-3/4 blotter, 54 entries
   with LP path data, modelled as "enter one cycle late"):
     4 %/min   -> rugs avoided $259.73, winners killed  $0.00, net +$259.73
     2 %/min   -> $303.41 avoided, $13.79 killed, net +$289.62
     15 %/min  -> $153.53 avoided, $0.00 killed, net +$153.53
     46-min @4% -> $386.45 avoided, $44.81 killed, net +$341.65
   4 %/min is the pick: the widest threshold that still killed zero winners
   in the replay. 7 mints were uncoverable (fail-open, +$10.37).

   SUCCESS CRITERIA (all four hold to call the mechanism validated):
     R1 precision — rug-class net per trip improves from the -$15.10 baseline
        to >= -$10.00, AND rug-class trips per day fall below half of 9.1/day
        (< 4.6/day).
     R2 no collateral — pivot-class trips stay >= 60% of 7.4/day (>= 4.4/day)
        and pivot-class net stays >= $0.
     R3 net — total window net > -$150 (replay predicts ~-$132 from -$392).
     R4 exercised — every entry carries >= 2 LP samples and >= 1
        entry_confirmation record; zero entries whose verdict was
        'insufficient'. A stage that never fires makes the experiment VOID,
        not passed.

   INVALIDATION (window void, not failed): entries/day below 50% of the
   window-3 entry rate, or any non-gate change landing inside the window
   (then restart #5 starts clean). The stage delays entry by one cycle, so a
   starved window measures discovery churn, not gate quality.

   Known risk, stated before the data: the runner enriches only OPEN
   positions, so a pending mint would get its second sample only if it
   reappeared in discovery. Pending mints are therefore re-enriched
   explicitly and expire after 900 s (entry_pending_expired) rather than
   entering on a stale pair. If expiry exceeds 80% of pendings, the stage is
   starving and R3/R4 cannot be read as gate quality.

   Decision rule after day 7:
     all four  -> keep the gate armed; restart #5 carries the next single
                  change (candidate: venue-aware exit slippage).
     R1 fails  -> the pre-entry signal is not there; revisit only with a
                  different LP-source class (not GoPlus).
     R2 fails  -> the gate is killing winners; lower the threshold to the
                  replay's zero-collateral point or drop the stage.
     void      -> fix discovery sampling first; do not re-arm.

