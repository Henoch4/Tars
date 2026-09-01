"""ML signal pipeline v2 (TRADING_MODEL_ROADMAP.md spike follow-up).

Design sources:
  - features: qlib Alpha158 recipe (model-inspiration/qlib/contrib/data/loader.py),
    windows adapted from [5,10,20,60] days to [12,24,72,168] hours
  - labels: mlfinlab triple-barrier + average-uniqueness weights
    (model-inspiration/mlfinlab/mlfinlab/labeling/labeling.py)
  - trade filtering: AFML meta-labeling -- a secondary model predicts whether
    the primary model's trade succeeds; only high-conviction trades are taken
  - gate: the repo's own src/validation.py (walk-forward + Calmar bar),
    5+3 bps per side, same cost model as reports/ml-spike-2026-08-22.md
"""
