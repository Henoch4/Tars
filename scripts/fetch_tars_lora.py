#!/usr/bin/env python3
"""Fetch the trained tars-lora adapter into models/kaggle-tars-lora/.

The trained LoRA adapter (35 MB) is versioned in its own GitHub model repo
(github.com/Henoch4/tars-lora); the Tarstrade monorepo deliberately does NOT
track model binaries (see model-inspiration/ pattern + RETRAINING_POLICY.md).

This script pulls the adapter + tokenizer on demand so the pipeline can load
the model without bloating git. Safe to re-run — it refetches every file
(no cache; the release is 6 small files). Base model is NOT fetched here
(~1 GB, loads from Hugging Face at inference time via transformers/peft).

Usage:
    python scripts/fetch_tars_lora.py

Requires git protocol access to github.com (https). No auth needed for a
public repo.
"""
from __future__ import annotations

import os
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEST = REPO / "models" / "kaggle-tars-lora" / "lora_model"
BASE_URL = "https://github.com/Henoch4/tars-lora/raw/main/lora_model/"

FILES = [
    "adapter_config.json",
    "adapter_model.safetensors",
    "chat_template.jinja",
    "tokenizer.json",
    "tokenizer_config.json",
]

def fetch(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    tmp.replace(dest)

def main() -> int:
    if not DEST.exists():
        DEST.mkdir(parents=True)
    for name in FILES:
        url = f"{BASE_URL}{name}"
        dest = DEST / name
        print(f"  {name} ...", end="", flush=True)
        try:
            fetch(url, dest)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f" FAILED ({exc})")
            continue
        size = dest.stat().st_size
        print(f" ok ({size / 1e6:.1f} MB)" if size > 1e5 else f" ok ({size} B)")
    print(f"\nadapter at {DEST.relative_to(REPO)}/")
    print("base model loads from Hugging Face at inference time (see README).")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)