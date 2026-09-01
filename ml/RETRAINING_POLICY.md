# Model retraining & update policy

**Applies to:** any ML model from this pipeline that clears the validation
gate and enters shadow/paper trading.
**Status at time of writing (2026-08-23):** v2 (Alpha158 features +
triple-barrier + meta-labeling) did NOT clear the gate
(`reports/ml-v2-2026-08-23.md`). Until a model clears, there is nothing to
update on a schedule — this policy activates on the first model that does.

---

## Cadence

| Cadence | Action | Why |
|---|---|---|
| **Weekly** | Retrain on the rolling 2y window: same features, same labels, same hyperparameters — only newer data | ~168 new bars/symbol/week is enough new information to matter in crypto regimes; training is minutes on CPU. Daily adds ~24 bars = noise-chasing |
| **Monthly** | Re-run the full validation gate (Calmar bar, PBO) on the freshly retrained model | A model that cleared two months ago may have quietly decayed. The gate, not training loss, decides whether the new version keeps running |
| **Quarterly (at most)** | Re-specify: new features, labels, or thresholds | Every spec change is a new bet against the overfitting statistics — log it as a new combination tried (PBO discipline, `src/validation.py`) |
| **Trigger-based** | Retrain + investigate when drift fires | Feature drift (KS test vs. training distribution) or low regime-detector confidence — crypto can shift faster than the calendar |

## Rules that outrank the schedule

1. **No silent model swaps.** Every retrained version runs in shadow next to
   the incumbent for a few days; the swap or rollback is written to the audit
   trail (`audit_log.jsonl`). A retrain is a hypothesis, not an upgrade.
2. **Retrain ≠ re-specify.** Refreshing the same model on new data is routine.
   Touching features/labels/thresholds because last month was bad is how you
   overfit the recent past — that instinct is what PBO tracking exists to
   catch.

## First activation checklist

- [ ] Model cleared `validation_report` gate OOS (Calmar ≥ 1, PBO ≤ 0.5, with
      OOS evidence — the vacuous-pass fix makes "never traded" a fail)
- [ ] Shadow mode ≥ 2 weeks logging to the audit trail (new entry type, e.g.
      `ml_shadow_prediction` — see ML_ROADMAP_REVISED.md Phase 5.3)
- [ ] Version stamp every artifact: feature spec hash + training window end +
      git commit, so any live decision can be traced to the exact model
- [ ] Retrain job scheduled (cron / VPS scheduler) with failure alerting —
      same pre-flight discipline as the trading loop itself

---

## Request & resource use limits

### State now (research/offline — v2 did not clear the gate)

- **Live inference: none.** No model is deployed, so there are zero per-cycle
  requests, zero API keys in use, and zero recurring cost.
- **Training runs: on-demand, local CPU.** ~2–4 min per symbol (LightGBM,
  300 trees, ~12k rows × 87 features); no GPU, no Colab/Kaggle quota touched.
- **Data requests: one-off bursts only.** `scripts/fetch_history.py` pulls
  2y of 1h candles + settled funding per symbol from **unauthenticated
  public endpoints** (OKX/Binance). At 4 symbols × 1h bars this is a few
  thousand requests once, refetchable anytime — far below public rate
  limits; no paid data vendor, no auth, no keys.
- **No LLM dependency anywhere in the pipeline** — the model is pure local
  LightGBM; nothing about it bills per request.

### When the model is working properly (shadow → paper → live)

- **Inference: local, sub-10ms, ~10MB in memory.** Prediction happens in the
  bot's process from data it already fetched — the model itself issues **no
  network requests at all**.
- **Per-cycle data budget: candles + funding only.** Feature windows need at
  most 168h of 1h OHLCV + latest funding per symbol. That is ~2 requests per
  symbol per cycle on top of the existing OKX CLI market fetch — comfortably
  inside public-endpoint rate limits at the bot's cycle cadence (resolve the
  CLI-vs-CCXT history question per ML_ROADMAP_REVISED.md Phase 0.2 before
  wiring).
- **Weekly retrain: local CPU minutes**, scheduled on the same host as the
  trading loop; no GPU quota, no external service.
- **Hard usage limits the model cannot exceed (by design):**
  - every signal passes the **risk gate** (`src/execution/risk_gate.py`) —
    the model can only vote, never size past limits or bypass collars
  - live weight is capped and graduated (start 0.5, cap 2.0 — roadmap
    Phase 6), and can be zeroed via the existing `enabled_signals`
    allowlist without a deploy
  - order rate/notional/daily-loss limits are enforced independently of
    model confidence; kill switch halts everything including the model
  - **no withdrawal capability, ever** — agent keys are trade-scoped
- **Cost ceiling: $0 marginal.** All of the above runs on infrastructure the
  bot already needs; the only real spend is the operator's time.

---

## Model-trust safeguards ("anti-hallucination")

The core model (LightGBM) cannot invent facts like an LLM — its equivalent
failure mode is **staying confidently wrong**. v2 already showed one:
meta-model confidence did not rank trades (win rate fell as the threshold
rose — `reports/ml-v2-2026-08-23.md`). Four modes to defend against:
calibration drift, feature drift, regime novelty, and state hallucination
(any agent "remembering" positions instead of reading the state store).

### State now (nothing deployed)

Only risk is research self-deception. Defenses already in the repo — keep
using them: frozen feature spec, PBO ledger of every combination tried,
version-stamped reports, and the gate's refusal to bless a never-traded
model. Every feature/threshold change is logged as a new combination,
never silently.

### When working properly (live) — never trust the model's self-report

1. **Calibration monitor**: rolling predicted-confidence vs realized win
   frequency; divergence past tolerance → auto-cut live weight. Catches the
   v2 failure mode directly.
2. **Drift monitor**: KS test per feature vs training window (roadmap
   Phase 6); drift → flat (fail-safe defaults, design doc §4).
3. **Novelty guard**: market state far from training distribution → no
   trade regardless of confidence.
4. **Deterministic state**: risk gate queries the state store, never a
   model's or agent's claimed exposure (design doc §2.C).
5. **Bounded authority**: model votes, gate sizes — inflated confidence
   cannot buy size past the caps.
6. **Versioned predictions**: every signal logged with its model version;
   degradation is traceable and reversible via `enabled_signals` (weight to
   zero, no deploy).
7. **LLM layers (if ever added)**: advisory-only until a track record
   exists (design doc §8); LLM reads state read-only.

---

*Source: post-v2 review session 2026-08-23; consistent with
TRADING_MODEL_ROADMAP.md Phase 7 and freqtrade FreqAI's live-retrain pattern
(model-inspiration/freqtrade/freqtrade/freqai/).*

---

## Addendum 2026-08-24 — funding-carry model: cadence & request-limit specifics

Anticipates the funding-path model (target #1) clearing, so these activate on first
clearance alongside the table above — a pre-written answer to "how often / how much"
for this model specifically.

### Retraining cadence (funding-specific adjustments to the table above)

- **Weekly** is genuinely sufficient — funding regimes persist for weeks to months, and a
  7d-horizon label means meaningful new *matured* label information arrives with a week's
  lag. Daily retraining would add only ~3 funding realizations/symbol/new-day of information
  while multiplying churn and swap-risk (violates rule #1's no-silent-swaps spirit).
- **Retrain timing:** schedule just after an 08:00 UTC funding settlement so the newest
  realization is in — but the most recent ~7 days of rows carry immature 7d-horizon labels
  and MUST stay excluded from training (the dataset builder's valid-label mask already does
  this; carry the same mask into the retrain path).
- **Tail regimes age out of a rolling window:** the delisted cohort (LUNA/FTX funding
  spirals) is the tail-regime anchor, and a rolling 2y window will silently drop it as time
  passes. "Not being wrong in a funding collapse" is part of the job — decide explicitly
  whether to pin those rows or extend the window. Never let the window do it silently.
- Quarterly at most re-specification remains, each logged as a new PBO-ledger combination.

### Request & resource limits (extended data tier, as of 2026-08-24)

Research state (current — no model deployed):
- **Extended tier fetch (one-off):** ~11,000 requests total → 70 symbols × ~35 pages
  (funding 4y + perp/spot 2y) + ~420 pages × 20 TIER1 for the 2y premium index + ~35 for
  delisted. Throttled at ≤8 req/s (SLEEP=0.12), unauthenticated public Binance endpoints,
  no keys. Reruns cache per file (`--refresh` refetches). *(Correction 2026-08-24: the
  premium-index history pages never complete — that endpoint 404s from this environment
  and now fails fast after the first attempt — so an actual full run is ~2,500 requests,
  not ~11,000.)*
- **Daily archiver (`CRON=1`):** ~230 requests/day → 1 funding + 1 perp + 1 spot page per
  symbol + 1 premium page per TIER1 symbol (5m points). Under a minute of runtime, $0.
- **Training:** local CPU minutes (LightGBM; extended dataset ~150k rows × 29 cols), no GPU.
- **No LLM dependency anywhere** — nothing bills per request.

Live state (unchanged from the main body, restated): the model itself issues **zero
network requests** — inference is local sub-10ms from data the bot already fetched; the
traded list stays BTC/ETH/SOL/BNB (the 70-symbol tier is research pooling, not live
footprint), so per-cycle cost is ~2 requests/symbol on top of the existing fetch, +1
batched cross-sectional premium pull if rank features are used. Weekly retrain = local CPU
minutes on the trading host. Hard caps unchanged: risk gate, graduated live weight cap,
`enabled_signals` zero-out, order/notional/daily-loss limits independent of confidence,
kill switch halts the model, no withdrawal capability ever.

---

## Addendum 2026-08-30 — tars-lora trained; canonical model home

The first training run that cleared the data-integrity bar succeeded on Kaggle
free T4 (`henchokumagbe/tars-lora`, 2026-08-30): QLoRA fine-tune of
**Qwen2.5-0.5B-Instruct** (rank 16, 4-bit base), 4,185 balanced rows, 1,000 steps
/ 1.91 epochs, checkpoints every 500. It answers the strategy's one question —
**"will 7d carry clear costs?" → `yes`/`no`** — with correct eval behavior on the
matched prompt format (positive funding → `yes`, negative → `no, they won't cost`).

### Canonical model home: `github.com/Henoch4/tars-lora`

Model binaries are **gitignored in this repo** (same policy as `data/`). The
trained adapter + tokenizer + model card + rerunnable training notebook live in
their own GitHub repo — `github.com/Henoch4/tars-lora`.

- Fetch the adapter on demand (35 MB): `python scripts/fetch_tars_lora.py`
  → `models/kaggle-tars-lora/lora_model/`
- Loading recipe + base-model requirement: `models/kaggle-tars-lora/README.md`
- During inference the base is `unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit` —
  **the same 4-bit variant used at training.** Loading this adapter on the fp16
  base shifts the quantization and flips marginal cases (~7/10 measured), so
  the base choice is part of the served model contract.

### What this changes for the cadence above

- This is an **LLM/advisory layer** — it does not replace the LightGBM carry
  model, and it cannot override the risk gate (`src/execution.py
  RiskGate/OrderExecutor`). Gate authority is unchanged.
- Its retraining cadence (notebook is rerunnable on Kaggle free tier) is
  **trigger-based**, not weekly: retrain when the funding regime the adapter was
  fit on stops matching live input distributions (drift monitor), or when a
  material label-spec change lands (`lora_train.jsonl` builder inputs). Cost is
  ~25 min of free T4 quota per run.
- Version discipline: bump a tag on `tars-lora` per release; the commit SHA the
  adapter was built from stays in that repo's model card. Do not retrain on a
  silent swapped adapter.
- Regression guard for a future swap: 5 yes + 5 no rows sampled from
  `lora_train.jsonl` (the Kaggle eval cell format is the reference prompt
  format); the new adapter must flip no more than the incumbent on that set.
