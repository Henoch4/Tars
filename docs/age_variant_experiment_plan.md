# Memecoin satellite experiment-and-iteration plan

## Purpose

Tarstrade learns from rejected and accepted candidates without weakening the
production memecoin satellite. Every strategy change is tested as a separate,
paper-only experiment with an immutable configuration.

## Current experiment: `age_variant_v1`

- **Baseline:** the current core satellite exactly as it runs today.
- **Variant:** the same discovery snapshot and hard-safety path, plus one rule:
  token age must be at least 1,800 seconds (30 minutes).
- **Paper notional:** $25 per hypothetical position.
- **Maximum open positions:** 3 per book.
- **Comparison:** identical mint and identical discovery timestamp.
- **Promotion:** never automatic; this experiment cannot trade live.

## Collection rules

The experiment configuration is frozen from its start. Every decision stores
the causal data snapshot, timestamp, baseline decision, variant decision,
entry assumptions, exit assumptions, costs, and failure reason where relevant.

No threshold, exit rule, cost model, or position cap may be changed during the
run. Any change creates a new experiment version.

## Continuous monitoring

Operational monitoring runs every cycle and does not retune the strategy. It
tracks:

- Candidate and rejection funnel
- Missing or contradictory data
- Baseline-versus-variant decisions
- Open positions and exposure
- Exit triggers and failed sells
- Slippage, fees, and liquidity impact
- RPC/provider failures and service health
- Drawdown and consecutive-loss stopping rules

Monitoring may pause the experiment for safety. It may not silently alter the
rules.

## Milestone reports

Generate a report at **50, 100, 250, and 500 completed paper trades**. Each
report is diagnostic only; it does not promote or retune the variant.

Reports include:

- Net PnL and expectancy after costs
- Baseline versus variant versus buy-and-hold
- Win rate, profit factor, and maximum drawdown
- Failed-sell and total-loss rates
- Results by age, liquidity, rejection reason, and market regime
- Chronological first-half versus second-half results
- Results with the largest winners removed
- Confidence intervals or bootstrap ranges
- Data-quality and provider-reliability summaries

## Final decision at 500 trades

After the pre-registered sample is complete, produce the full review package
and choose exactly one outcome:

1. **Promote:** only with robust, cost-adjusted outperformance and no material
   safety degradation.
2. **Iterate:** create a new version such as `age_variant_v2` with a written
   reason and a new frozen configuration.
3. **Discard:** permanently stop the variant when it underperforms, creates
   unacceptable failure rates, or provides no durable evidence.

The core satellite is never changed automatically by experiment results.
