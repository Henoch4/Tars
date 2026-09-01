import gradio as gr
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

BASE = "unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit"
SUFFIX = " -> will 7d carry clear costs?"
LOAD = "cpu"

tokenizer = AutoTokenizer.from_pretrained(BASE)

try:
    model = AutoModelForCausalLM.from_pretrained(BASE, device_map=LOAD)
    model = PeftModel.from_pretrained(model, "lora_model")
    BACKEND = "4-bit"
except Exception:
    model = AutoModelForCausalLM.from_pretrained(
        "Qwen/Qwen2.5-0.5B-Instruct", device_map=LOAD, torch_dtype=torch.float32
    )
    model = PeftModel.from_pretrained(model, "lora_model")
    BACKEND = "fp16 fallback"

model.eval()


def ask(prompt: str):
    if SUFFIX not in prompt:
        prompt = prompt.strip() + SUFFIX
    inputs = tokenizer(prompt, return_tensors="pt").to(LOAD)
    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=8,
            temperature=0.0,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    answer = tokenizer.decode(out[0], skip_special_tokens=True)[len(prompt) :].strip()
    if not answer:
        answer = "(no completion — try a longer funding line)"
    return f"{answer}\n\n(backend: {BACKEND})"


gr.Interface(
    fn=ask,
    inputs=gr.Textbox(
        label="Funding line",
        value="Funding 0.0003 basis 2.1 vol 0.0184 ret 0.021 7d_mean 0.0002 z 1.2",
    ),
    outputs=gr.Textbox(label="Will 7d carry clear costs?"),
    title="Tars-Lora — Funding Persistence Judge (Qwen2.5-0.5B QLoRA)",
    description=(
        "LoRA fine-tune of Qwen2.5-0.5B-Instruct on 4,185 real funding rows. "
        "Paste a line in the training format: "
        "`Funding <val> basis <val> vol <val> ret <val> 7d_mean <val> z <val>`"
    ),
).launch()