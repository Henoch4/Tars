"""Asset -> audit-chain router (multichain dropdown logic, server-side).

Rule: the audit trail lives where the asset lives.
- Solana-native assets (tokenized equities ``AAPLx``/``TSLAx``/``NVDAx``,
  devnet ``dAAPLx``..., future Solana spot/mint legs) -> ``"solana"``.
- CEX instruments (``BTC-USDT-SWAP``, ``ETH-USDT`` ...) -> ``"evm"``.

Precedence: explicit env allowlists > suffix rules > default (evm, which
preserves pre-Solana behavior for every existing instrument).

Env overrides (comma-separated, exact match, case-insensitive):
  SOLANA_ASSETS=AAPLx,TSLAx,MYTOKEN
  EVM_ASSETS=SOL-USDT-SWAP

Stdlib only — safe to import from ``agent.py`` and ``dual_logger.py``.
"""
from __future__ import annotations

import os

SOLANA = "solana"
EVM = "evm"


def _env_set(name: str) -> set[str]:
    raw = os.environ.get(name, "")
    return {p.strip().upper() for p in raw.split(",") if p.strip()}


def chain_for_asset(asset: str | None) -> str:
    """Return ``"solana"`` or ``"evm"`` for an asset symbol. Never raises."""
    try:
        sym = (asset or "").strip()
        upper = sym.upper()
        if not upper:
            return EVM
        if upper in _env_set("EVM_ASSETS"):
            return EVM
        if upper in _env_set("SOLANA_ASSETS"):
            return SOLANA
        # CEX instruments: BTC-USDT-SWAP, ETH-USDT, ... (contain '-' or end SWAP)
        if upper.endswith("-SWAP") or "-USDT" in upper or "-USD-" in upper:
            # SOL-USDT-SWAP is a CEX perp even though base is SOL.
            return EVM
        # Tokenized equities: AAPLx / TSLAx / NVDAx (suffix-x convention),
        # devnet dAAPLx / dTSLAx, bare Solana mints leg names.
        if upper.endswith("X") and "-" not in upper:
            return SOLANA
        if upper.startswith("D") and upper.endswith("X") and "-" not in upper:
            return SOLANA
        return EVM
    except Exception:
        return EVM


def is_solana_asset(asset: str | None) -> bool:
    return chain_for_asset(asset) == SOLANA
