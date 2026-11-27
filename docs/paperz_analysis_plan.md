# tars-paperz analysis plan — SOL funding_persistence_z paper cohort

Status: DRAFT (2026-11-01) — the analysis contract for the ≥30-trade paper
cohort collected by `tars-paperz.service` on the VPS. Mirrors the structure
of the G1 postmortem (`g1_postmortem_and_restart6_plan.md`).

## 0. What this cohort is

Forward, cost-honest, param-locked paper validation of the single edge that
ever cleared the validation gate: `funding_persistence_z` on SOL-USDT-SWAP
(OOS Sharpe 2.206, Calmar 16.125, net +13.9% on 2y history, 28 trades).

- Data: `/opt/tars/paperz/data/paper_sol_state.json` (state),
  `paper_sol_ledger.jsonl` (open/close events), `paper_sol_audit.jsonl`
  (every cycle's observed funding rate, price, signal, rationale).
- Params locked at z≥1.5 / window 5 / frac 0.8 / mild ±0.0003. **Never
  re-tuned on this cohort's results.** A fail here means "the edge did not
  survive forward," not "re-fit until it passes."
- Costs modeled: 5 bps fee + gaussian slippage per side (~16 bps round
  trip), break-even carry floor ≈3.05 bp/period, direction-aware funding.
- Success gate (§2.2 of MASTER_BUILD_PLAN): ≥30 closed trades AND net
  carry > $0 after all costs AND no kill/stop anomalies.

## 1. Pre-analysis trade distribution (gate before reading tea leaves)

Before any angle-specific conclusions:

1. Reconcile ledger vs state: every `open` has a matching `close` or a live
   open in state; `net_carry_usd` in state equals the sum of closed
   `net_pnl_usd` (attribute any drift to restarts — same method as the G1
   reconciliation).
2. Confirm every trade's metadata completeness: direction, entry/exit
   fills, fees, realized slippage, funding accrued, hold periods, z and
   persistence at entry, BTC regime tag. A trade missing metadata is
   excluded from that angle (not imputed).
3. Time-frame statement: cohort start, end, funding-periods elapsed —
   the same discipline the full-blotter analysis needed when the satellite
   kept trading past the G1 clock.

## 2. Analysis angles

### A2.1 — Carry decomposition (primary)
Split every closed trade's net PnL into three additive parts:
`npnl = funding_collected − entry_fee − exit_fee − slippage_cost + price_move`
- Question: does funding carry do the work (edge = carry), or is the PnL
  actually price reversion wearing a funding costume?
- If positive trades are price-move-driven, the validated edge is not the
  validated edge — flag for the validation-gate owner before any live plan.

### A2.2 — Z-magnitude dose–response (primary)
Bucket closed trades by entry z (e.g. 1.5–2.0 / 2.0–2.5 / ≥2.5): does
average net PnL increase monotonically with z?
- Monotonic dose–response is the strongest single piece of evidence that
  the signal is real rather than spurious.
- With 30 trades this is directional only — treat as hypothesis, same way
  the G1 windows are treated. Needs the cohort's consent before being
  called a "rule."

### A2.3 — Regime dependence (requires BTC tag — see §3)
Tag every entry with BTC's drawdown-from-90d-high at entry. Analyze:
cohort performance when BTC in drawdown vs near high.
- The validated edge came from a different macro era; a cohort entirely
  inside one regime is a regime-stamped result (state this in the final
  report like the G1 postmortem does).

### A2.4 — Hold-time profile
Net PnL vs hold periods (per exogenous max-hold cap of 21).
- Did any position reach the 21-period cap, and did capping help or cost?
  Compare realized PnL at cap-exit vs the mark just before (counterfactual).

### A2.5 — Cost realism audit
- Realized slippage draws vs the modeled 3 bp/side: mean, max, share of
  trades where slippage exceeded 2× the model.
- Realized funding per period vs the running estimate seen at entry.
- Break-even floor: for every entry, confirm `|funding_rate| ≥
  break_even_funding_rate` held at entry time (guaranteed by code; verify
  in data — the floor's implementation must match its tests).

### A2.6 — Loss clustering / drawdown character
Sequence the closed trades: are losses contiguous in funding-time
(regime cluster) or spread? Integer measure: longest loss streak, max
consecutive-losing-funding-rate run, and the calendar gap between the two
worst consecutive trades.

### A2.7 — Signal availability context (not an edge claim)
How many cycles, how many signal fires, how many NEUTRAL rationales by
type (persistent-band, insufficient-history, mild-band). Context for
"how often does this edge show up," not part of the PnL claim.

## 3. Data-capture requirements (implemented 2026-11-01)

At every ENTRY, the following block is snapshotted into the trade record:
- SOL funding running estimate + period id (already present)
- z, persistence, apply confidence (already present)
- **BTC regime tag**: BTC-USDT-SWAP last price, 90-day high from daily
  candles, and `drawdown_from_90d_high = btc_px/btc_high90 − 1`
  (fails open to `null` — a metadata gap never blocks the edge; gaps are
  counted in §1 completeness check instead).

Without §3, A2.2/A2.3 are permanently partial. This is why the tag ships
WITH the cohort start, not after.

## 4. Verdict discipline

- PASS/FAIL is stated in the same one-line form as the G1 window verdicts:
  metric vs required threshold, with cohort dates.
- Both PASS and FAIL end with a regime stamp (macro conditions of the
  cohort window) and a re-test cadence note (4–8 weeks or after regime
  change, mirroring G1 rules).
- On FAIL: root-cause path is A2.1 → A2.5 → A2.3 (carry present? costs
  wrong? regime wrong?), each documented before touching any parameter.
- Nothing from this cohort is actionable until the verdict record exists
  in `LIVE_TRADING_PLAYBOOK.md` (or successor), § by §.

## 5. Open questions for the cohort (parked, not blocking)

- OKX returns the running per-period funding estimate while history is
  settled rates; signal input mixes the two. If the cohort fails, revisit
  whether running-estimate-at-entry is the cause (A2.5 will show if the
  estimate drifts materially from the settled rate).
- Cohort is one venue (OKX demo data, real prices). No cross-venue
  carry comparison is possible in this cohort — note in verdict.
