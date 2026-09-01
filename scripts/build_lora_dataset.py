#!/usr/bin/env python3
"""Build the real training JSONL for the Kaggle QLoRA funding-path model.

Input : data/carry/dataset.csv (scripts/build_carry_dataset.py — purged
        walk-forward, leakage-safe)
Output: data/carry/lora_train.jsonl — one {"text": ...} per row, formatted
        for SFTTrainer's dataset_text_field="text", balanced on y_win.

Label choice (ML_ROADMAP_REVISED.md, 2026-08-23 findings):
  * 72h hold NEVER clears 2x a 35bps round trip (max ~65bps/72h < 70bps),
    so the trade-evidence label is the 7-DAY cumulative funding.
  * y_win = y_sum_7d_bps > 2x COST_BPS is the "does carry clear costs over
    the natural holding period" question the model must answer.
  * Naive persistence loses to predicting zero at 7d (RMSE 26.1 vs 25.1),
    so "yes" must come from the feature text, not from marginal-base-rate
    guessing. We balance positives with an equal-draw of negatives so the
    SFT loss cannot cheat by always answering the majority class.

Features included are the strictly-trailing ones from the carry builder;
NaN-laden columns (btc_funding_regime is NaN on the whole train set in this
build) are excluded. Rows with NaN in any kept feature are dropped BEFORE
balancing so the label balance is computed on complete rows only.

Balancing is on the TRAIN+usable split only; nothing is leaked.

Run: python scripts/build_lora_dataset.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
CSV = REPO / "data" / "carry" / "dataset.csv"
OUT = REPO / "data" / "carry" / "lora_train.jsonl"
NEG_POS_RATIO = 2.0   # sample 2 negatives per positive after complete-row drop
SEED = 42

# Strictly-trailing features with full coverage on the current build.
FEATURES = [
    "f0",           # current funding rate (8h-normalized)
    "f_mean3d",     # 3d mean funding
    "f_mean7d",     # 7d mean funding
    "f_z30d",       # 30d rolling z-score of funding
    "f_hot7d",      # 7d "hot" share (fraction above elevated band)
    "basis_bps",    # perp-spot basis, bps
    "basis_mean24h",
    "vol_24h",
    "vol_72h",
    "ret_24h",
    "ret_72h",
    "x_btc_z",      # BTC funding z (cross-asset tell) — has NaNs, dropped per row
]

# Text template the SFT model must learn. Keep the *question* the same shape
# the notebook's demo used so Cell 5 prompts stay valid.
TEMPLATE = (
    "Funding {f0} basis {basis_bps} vol {vol_24h} "
    "ret {ret_24h} 7d_mean {f_mean7d} z {f_z30d} "
    "-> will 7d carry clear costs? {ans}"
)


def main() -> None:
    df = pd.read_csv(CSV)
    tr = df[(df["split"] == "train") & (df["usable"] == True)].copy()  # noqa: E712
    tr = tr.dropna(subset=FEATURES)

    pos = tr[tr["y_win"] == 1]
    neg = tr[tr["y_win"] == 0]
    n_neg = int(np.ceil(pos.shape[0] * NEG_POS_RATIO))
    neg_s = neg.sample(n=n_neg, random_state=SEED)
    sample = pd.concat([pos, neg_s]).sample(frac=1.0, random_state=SEED)

    records = []
    for _, r in sample.iterrows():
        ans = "yes" if r["y_win"] == 1 else "no"
        text = TEMPLATE.format(
            f0=_fmt(r["f0"]),
            basis_bps=_fmt(r["basis_bps"]),
            vol_24h=_fmt(r["vol_24h"]),
            ret_24h=_fmt(r["ret_24h"]),
            f_mean7d=_fmt(r["f_mean7d"]),
            f_z30d=_fmt(r["f_z30d"]),
            ans=ans,
        )
        records.append({"text": text})

    OUT.write_text("\n".join(json.dumps(r) for r in records))

    print(f"rows complete (train+usable): {len(tr)}")
    print(f"positives: {len(pos)}  negatives drawn: {n_neg}  total: {len(records)}")
    yes_cnt = sum(1 for r in records if r["text"].endswith("? yes"))
    print(f"y_win rate in JSONL: {yes_cnt / len(records):.3f}")
    print(f"wrote {OUT}  ({OUT.stat().st_size / 1024:.1f} KiB)")
    print("sample:")
    for r in records[:3]:
        print(" ", r["text"])


def _fmt(x: float) -> str:
    if isinstance(x, (int, float)) and np.isfinite(x):
        return f"{x:.4f}".rstrip("0").rstrip(".")
    return "0"


if __name__ == "__main__":
    main()