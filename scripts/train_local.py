#!/usr/bin/env python3
"""Local training wrapper — Windows CPU, $0, no GPU/Kaggle needed.

Runs the exact same pipeline as Kaggle, but locally on CPU (LightGBM).
Uses model-inspiration/ repos as feature/model library, not as deps.

Usage:
  python scripts/train_local.py              # all symbols BTC,ETH,SOL,BNB
  python scripts/train_local.py --symbols BTC  # smoke
  python scripts/train_local.py --quick        # 1 fold, for testing without GPU

Prereqs (once):
  pip install -r requirements.txt
  pip install lightgbm pandas numpy  # already in ml/* via model-inspiration/LightGBM
  # For HF QLoRA locally (optional, needs GPU): pip install unsloth transformers peft datasets accelerate trl bitsandbytes

This wraps ml/pipeline.py so you have ONE entrypoint for both paths.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

def main() -> int:
    ap = argparse.ArgumentParser(description="Tarstrade local training (Kaggle mirror)")
    ap.add_argument("--symbols", default="BTC,ETH,SOL,BNB", help="comma-separated, e.g. BTC")
    ap.add_argument("--quick", action="store_true", help="quick smoke (passes --symbols BTC and sets quick flag)")
    args = ap.parse_args()

    symbols = "BTC" if args.quick else args.symbols
    cmd = [sys.executable, "-m", "ml.pipeline", "--symbols", symbols]
    print(f"[local] {' '.join(cmd)}  (CPU, ~2-4 min/symbol, uses model-inspiration/timesfm, LightGBM, etc. as library)")
    print(f"[local] Data: data/*_1h_candles.csv + data/*_funding_binance.csv (from scripts/fetch_history.py)")
    print(f"[local] Gate: src/validation.py Calmar>=1.0 PBO<=0.5 — report in reports/ml-v2-*.md")
    result = subprocess.run(cmd, cwd=str(REPO))
    if result.returncode == 0:
        print("\n[local] Done. Check reports/ml-v2-*.md for selected_threshold_is OOS Calmar/PBO.")
        print("[local] Next: same symbols on Kaggle -> upload notebooks/tarstrade-finetune-kaggle.ipynb -> Run All (free T4).")
        print("[local] Weekly retrain: same command on rolling 2y — shadow 2 weeks before live weight (ml/RETRAINING_POLICY.md).")
    return result.returncode

if __name__ == "__main__":
    raise SystemExit(main())
