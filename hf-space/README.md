---
title: Tars-Lora
emoji: 📊
colorFrom: blue
colorTo: green
sdk: gradio
sdk_version: 5.9.1
app_file: app.py
pinned: false
---

# Tars-Lora — Funding Persistence Judge

Gradio demo for the Tars-Lora QLoRA adapter (Qwen2.5-0.5B-Instruct, rank-16 LoRA,
trained on 4,185 real funding rows).

It answers one question from a funding line:

`Funding 0.0003 basis 2.1 vol 0.0184 ret 0.021 7d_mean 0.0002 z 1.2 -> will 7d carry clear costs?`

Expected answer: `yes` (positive, persistent funding) or `no` (negative/decaying funding).

## Files
- `app.py` — Gradio app; loads the 4-bit base + adapter on CPU (fp16 fallback).
- `lora_model/` — the trained adapter (source: github.com/Henoch4/tars-lora).