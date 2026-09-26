"""Tarstrade Solana sidecar (ported from Stockulus). Pure modules import-safe; anchor/solana deps lazy."""
from typing import Any
from .bsm import bs_price, bs_price_merton, cost_of_carry, delta_merton, greeks, greeks_merton, implied_vol, hedge_order
from .stock_carry import carry_annualized, tokenized_stock_carry_signal, vault_9010_allocation, bsm_mispricing
from .regime_hmm import RegimeHMM, infer_regime_simple, dbc_action_for_regime, STATES
try:
    from .sol_signals import Signal as _SolanaSignal
    Signal: Any = _SolanaSignal
except Exception:
    Signal = None
__all__ = ["bs_price","bs_price_merton","cost_of_carry","delta_merton","greeks","greeks_merton","implied_vol","hedge_order","carry_annualized","tokenized_stock_carry_signal","vault_9010_allocation","bsm_mispricing","RegimeHMM","infer_regime_simple","dbc_action_for_regime","STATES","Signal"]
