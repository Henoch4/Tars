"""
tars-lora inference client for the Tarstrade autonomous trading agent.

Loads the QLoRA fine-tuned Qwen2.5-0.5B model and provides a clean interface
for the funding-carry decision: "will 7d carry clear costs?"
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Optional heavy imports - only loaded when actually used
torch: Any = None
AutoModelForCausalLM: Any = None
AutoTokenizer: Any = None
PeftModel: Any = None


def _ensure_imports():
    """Lazy-load heavy ML dependencies."""
    global torch, AutoModelForCausalLM, AutoTokenizer, PeftModel
    if torch is None:
        import torch as _torch
        from transformers import AutoModelForCausalLM as _AutoModelForCausalLM
        from transformers import AutoTokenizer as _AutoTokenizer
        from peft import PeftModel as _PeftModel
        torch = _torch
        AutoModelForCausalLM = _AutoModelForCausalLM
        AutoTokenizer = _AutoTokenizer
        PeftModel = _PeftModel


@dataclass
class CarryFeatures:
    """Features for the funding-carry model."""
    funding_rate: float          # Current funding rate (e.g., 0.0003 = 0.03%)
    basis_bps: float             # Perp-spot basis in basis points (e.g., 2.1)
    vol: float                   # Realized volatility (e.g., 0.0184)
    ret: float                   # Recent return (e.g., 0.021)
    funding_7d_mean: float       # 7-day mean funding rate (e.g., 0.0002)
    funding_z_score: float       # Funding z-score (e.g., 1.2)

    def to_prompt(self) -> str:
        """Convert features to the model's expected prompt format."""
        return (
            f"Funding {self.funding_rate:.6f} "
            f"basis {self.basis_bps:.1f} "
            f"vol {self.vol:.4f} "
            f"ret {self.ret:.4f} "
            f"7d_mean {self.funding_7d_mean:.6f} "
            f"z {self.funding_z_score:.1f} "
            f"-> will 7d carry clear costs?"
        )


@dataclass
class CarryDecision:
    """Result of the carry-clear-costs decision."""
    will_clear: bool             # True = yes, False = no
    confidence: float            # 0.0 - 1.0 (model's confidence)
    raw_answer: str              # Raw model output for audit


class TarsLoraClient:
    """
    Client for the tars-lora carry-cost classifier.

    Loads the 4-bit base model + LoRA adapter. CPU or GPU.
    The model answers: "will 7d carry clear costs?" -> "yes" / "no"
    """

    def __init__(
        self,
        base_model: str = "unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit",
        adapter_path: str = "tars-lora-repo/lora_model",
        max_new_tokens: int = 8,
        device: str | None = None,
    ):
        self.base_model = base_model
        self.adapter_path = adapter_path
        self.max_new_tokens = max_new_tokens
        self.device = device or ("cuda" if torch and torch.cuda.is_available() else "cpu")
        self._tokenizer: Any = None
        self._model: Any = None
        self._loaded = False

    def load(self) -> None:
        """Load the base model and LoRA adapter."""
        _ensure_imports()

        logger.info(f"Loading tars-lora: base={self.base_model}, adapter={self.adapter_path}")

        self._tokenizer = AutoTokenizer.from_pretrained(self.base_model)
        model = AutoModelForCausalLM.from_pretrained(
            self.base_model,
            torch_dtype=torch.float16,
            device_map="auto",
        )
        self._model = PeftModel.from_pretrained(model, self.adapter_path)
        self._model.eval()
        self._loaded = True
        logger.info("tars-lora loaded successfully")

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def predict(self, features: CarryFeatures) -> CarryDecision:
        """
        Predict whether 7-day carry will clear costs.

        Returns CarryDecision with will_clear (bool), confidence (float), raw_answer (str).
        """
        self._ensure_loaded()

        prompt = features.to_prompt()
        logger.debug(f"tars-lora prompt: {prompt}")

        inputs = self._tokenizer(prompt, return_tensors="pt")
        if self.device == "cuda" and torch.cuda.is_available():
            inputs = {k: v.cuda() for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                pad_token_id=self._tokenizer.eos_token_id,
            )

        # Decode only the new tokens
        input_len = inputs["input_ids"].shape[1]
        new_tokens = outputs[0][input_len:]
        answer = self._tokenizer.decode(new_tokens, skip_special_tokens=True).strip().lower()

        logger.debug(f"tars-lora raw answer: '{answer}'")

        # Parse yes/no from answer
        will_clear = "yes" in answer or "true" in answer
        # Simple confidence: 0.9 for clear yes/no, 0.5 for ambiguous
        confidence = 0.9 if ("yes" in answer or "no" in answer) else 0.5

        return CarryDecision(
            will_clear=will_clear,
            confidence=confidence,
            raw_answer=answer,
        )

    def predict_batch(self, features_list: list[CarryFeatures]) -> list[CarryDecision]:
        """Batch predict (sequential for now; can be optimized)."""
        return [self.predict(f) for f in features_list]


# Global singleton for reuse across cycles
_global_client: Optional[TarsLoraClient] = None


def get_tars_lora_client() -> TarsLoraClient:
    """Get or create the global tars-lora client."""
    global _global_client
    if _global_client is None:
        adapter_path = os.getenv("TARS_LORA_ADAPTER_PATH", "tars-lora-repo/lora_model")
        _global_client = TarsLoraClient(adapter_path=adapter_path)
    return _global_client


def predict_carry_clear(features: CarryFeatures) -> CarryDecision:
    """Convenience function for one-off predictions."""
    return get_tars_lora_client().predict(features)