#!/usr/bin/env python3
"""LOCAL dry-run of the tars-lora OOS eval pipeline — no GPU, no LLM.

Exercises every non-torch function in the eval kernel against the REAL
data/carry/dataset.csv using a deterministic mock score (logistic ramp on f0+basis).
This proves the pipeline mechanics, the vendored gate wiring, and the report
shape BEFORE the user uploads the 81 MB dataset to Kaggle.

Also stages the upload folder (models/tars-eval-kaggle/) via prep_eval_dataset.py
and asserts the staged copies are byte-identical to the repo sources, so "what we
tested here is what runs on Kaggle" is a checkable fact, not an assumption.

Usage:  python scripts/tars_lora_eval_dryrun.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from scripts.prep_eval_dataset import STAGE, main as stage_main  # noqa: E402

# Vendored copies must match sources byte-for-byte (what Kaggle runs == what we test).
stage_main()
assert (STAGE / "validation.py").read_bytes() == (REPO / "src" / "validation.py").read_bytes()
assert (STAGE / "eval_lib.py").read_bytes() == (REPO / "scripts" / "tars_lora_eval_lib.py").read_bytes()
print("\n[ok] staged copies byte-identical to repo sources")

# Import the gate and eval lib from the STAGED dir, exactly as the kernel will.
sys.path.insert(0, str(STAGE))
import validation  # noqa: E402  (vendored copy of src/validation.py)
import eval_lib as L  # noqa: E402  (vendored copy of scripts/tars_lora_eval_lib.py)

# --- 1. load + select OOS rows -------------------------------------------
df = pd.read_csv(STAGE / "dataset.csv")
rows = L.select_eval_rows(df)
print(f"\n[1] eval rows (test & usable & 6 feats): {len(rows)}")

# --- 2. prompts + MOCK score (real LLM runs only on Kaggle) --------------
prompts = L.build_prompts(rows)
print(f"[2] prompts built: {len(prompts)}  sample:\n     {prompts[0]}")

f0 = rows["f0"].to_numpy()
basis = rows["basis_bps"].to_numpy()
raw = (f0 / 1e-4) * 0.9 + (basis / 20.0) * 0.5
score = 1.0 / (1.0 + np.exp(-raw))          # mock P(yes): high funding & basis ramp
enter_model = L.binary_from_score(score)
enter_incumbent = L.incumbent_enter(rows)
print(f"[2] mock enter rate: {enter_model.mean():.3f} | incumbent enter rate: {enter_incumbent.mean():.3f}")

# --- 3. Q&A parse cross-check (generate path simulated) ------------------
fake = ["yes", " no", "unsure", "yes 7d carry clears", ""]
parsed = [L.parse_generated(x) for x in fake]
assert parsed == ["yes", "no", "other", "yes", "other"], parsed
# agreement of the binary rule vs a generated parse on the first 100 rows
agree = np.mean([L.parse_generated("yes" if s >= 0.5 else "no") == ("yes" if e else "no")
                 for s, e in zip(score[:100], enter_model[:100])])
print(f"[3] parse + binary agreement (sample 100): {agree:.2f}")

# --- 4. policies + net PnL ----------------------------------------------
net_model = L.policy_net_bps(rows, enter_model)
net_inc = L.policy_net_bps(rows, enter_incumbent)
print(f"[4] model trades  : {L.trades_summary(rows, enter_model)}")
print(f"    incumbent     : {L.trades_summary(rows, enter_incumbent)}")
assert np.all(net_model[~enter_model] == 0.0)

# --- 5. portfolio series + live gate ------------------------------------
_, s_model = L.portfolio_series(rows, net_model)
_, s_inc = L.portfolio_series(rows, net_inc)
report_model = L.gate_report(s_model)
report_inc = L.gate_report(s_inc)
print(f"[5] gate model    : {json.dumps(report_model, indent=1)}")
print(f"    gate incumbent: {json.dumps(report_inc, indent=1)}")
assert isinstance(report_model["cleared_for_paper_trading"], bool)

# --- 6. diagnostics -----------------------------------------------------
diag = L.diagnostics(score, rows["y_sum_7d_bps"].to_numpy())
diag.pop("deciles")
print(f"[6] diagnostics   : {json.dumps(diag, indent=1)}")
assert len(L.decile_table(score, rows["y_sum_7d_bps"].to_numpy())) == 10
assert -1.0 <= L.spearman(score, rows["y_sum_7d_bps"].to_numpy()) <= 1.0

# --- 7. cost sensitivity -------------------------------------------------
costs = L.cost_sensitivity(rows, enter_model)
print(f"[7] cost sensitivity (model): {json.dumps(costs)}")
assert set(costs) == {"16", "35", "50"}

# --- 8. serialize full report (to temp, keeps repo clean) ---------------
full = {
    "eval_rows": len(rows),
    "y_win_positives_in_eval": int(rows["y_win"].sum()),
    "model": report_model,
    "incumbent": report_inc,
    "model_trades": L.trades_summary(rows, enter_model),
    "incumbent_trades": L.trades_summary(rows, enter_incumbent),
    "diagnostics": L.diagnostics(score, rows["y_sum_7d_bps"].to_numpy()),
    "cost_sensitivity": costs,
    "mock_score_rule": "logistic ramp on f0+basis (NOT the model — pipeline check only)",
}
out = Path(tempfile.gettempdir()) / "opencode" / "tars-lora-eval-dryrun.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(full, indent=2))
print(f"\n[done] dry-run report: {out}")
print("\nPipeline OK.")