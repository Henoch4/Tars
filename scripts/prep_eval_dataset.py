#!/usr/bin/env python3
"""Stage the files for the `tars-eval` Kaggle Dataset (upload step, run manually).

The eval kernel (`notebooks/tars-lora-eval.ipynb`) expects a Kaggle Input,
Dataset `tars-eval`, containing exactly:

    dataset.csv        # real carry dataset (~81 MB), eval rows = split=="test"
    validation.py      # COPY of src/validation.py — the live gate, single source
    eval_lib.py        # COPY of scripts/tars_lora_eval_lib.py — tested locally
    lora_model/        # the trained adapter + tokenizer (6 files)

Why copies: the kernel runs on Kaggle with no access to this repo, so the repo's
gate code must ride along. Patching them up front means what runs on Kaggle is
byte-identical to what was verified locally.

Usage:  python scripts/prep_eval_dataset.py
Then upload the `models/tars-eval-kaggle/` contents as a NEW private Kaggle
Dataset named `tars-eval` (Add Input -> Dataset -> New Dataset -> Upload).
"""
from __future__ import annotations

import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC_DATA = REPO / "data" / "carry" / "dataset.csv"
SRC_VALIDATION = REPO / "src" / "validation.py"
SRC_LIB = REPO / "scripts" / "tars_lora_eval_lib.py"
SRC_ADAPTER = REPO / "models" / "kaggle-tars-lora" / "lora_model"
STAGE = REPO / "models" / "tars-eval-kaggle"


def copy_if_present(src: Path, dst: Path) -> None:
    if not src.exists():
        raise SystemExit(f"MISSING {src} — cannot stage. Re-check paths.")
    shutil.copy2(src, dst)
    print(f"  {dst} ({dst.stat().st_size / 1e6:.1f} MB)" if dst.stat().st_size > 1e6
          else f"  {dst} ({dst.stat().st_size} B)")


def main() -> None:
    STAGE.mkdir(parents=True, exist_ok=True)
    adapter_dir = STAGE / "lora_model"
    if adapter_dir.exists():
        shutil.rmtree(adapter_dir)
    adapter_dir.mkdir(parents=True)

    print(f"Staging {STAGE.relative_to(REPO)}:")
    copy_if_present(SRC_DATA, STAGE / "dataset.csv")
    copy_if_present(SRC_VALIDATION, STAGE / "validation.py")
    copy_if_present(SRC_LIB, STAGE / "eval_lib.py")

    files = sorted(SRC_ADAPTER.iterdir()) if SRC_ADAPTER.exists() else []
    if not files:
        raise SystemExit(f"MISSING adapter in {SRC_ADAPTER} — run scripts/fetch_tars_lora.py first.")
    print(f"  lora_model/ ({len(files)} files)")
    for f in files:
        shutil.copy2(f, adapter_dir / f.name)
    for f in sorted(adapter_dir.iterdir()):
        print(f"    {f.name} ({f.stat().st_size} B)")

    total_mb = sum(f.stat().st_size for f in STAGE.rglob("*") if f.is_file()) / 1e6
    print(f"\nTotal staged: {total_mb:.1f} MB")
    print("""
Next (manual, in Kaggle):
  1. kaggle.com/datasets -> New Dataset -> name exactly: tars-eval  (private)
  2. Upload the CONTENTS of models/tars-eval-kaggle/ (keep this folder layout)
  3. In the eval notebook: Add Input -> Dataset -> select tars-eval
  4. Run the kernel (GPU T4, internet enabled; base model loads from Hugging Face)
""")


if __name__ == "__main__":
    main()