"""Tokenized-stock carry + 90/10 principal-protected allocation. Depends only on signals.Signal."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sol_signals import Signal


def _make_signal(*args, **kwargs):
    from .sol_signals import Signal  # deferred: avoids circular stock_carry<->signals import
    return Signal(*args, **kwargs)


def carry_annualized(div_yield: float, borrow_rate: float, funding: float, fee_drag: float = 0.0) -> float:
    """Carry (decimal) = q + b_collect - funding - fees. x10000 for bps."""
    return div_yield + borrow_rate - funding - fee_drag

def tokenized_stock_carry_signal(asset, spot, perp, borrow_rate, div_yield, funding, fee_drag=0.0,
                                 min_basis_bps=5.0, min_carry_bps=50.0) -> Signal:
    basis_bps=(perp-spot)/spot*10000 if spot>0 else 0
    carry_ann=carry_annualized(div_yield, borrow_rate, funding, fee_drag)*10000
    if abs(basis_bps)<min_basis_bps or abs(carry_ann)<min_carry_bps:
        return _make_signal(strategy="stock_carry",asset=asset,direction="NEUTRAL",confidence_bps=0,
                            entry_price=spot,rationale=f"basis {basis_bps:.1f}bps carry {carry_ann:.1f}bps — no trade",
                            metadata={"basis_bps":basis_bps,"carry_bps":carry_ann})
    direction = "LONG" if carry_ann > 0 and basis_bps > 0 else "SHORT"
    conf_base = 0.7 + abs(basis_bps)/200 + abs(carry_ann)/200
    conf = min(conf_base, 0.9)
    if direction == "SHORT":
        legs = {"spot": "SHORT", "dbc": "LONG"}
        rationale = f"negative carry {carry_ann:.1f}bps basis {basis_bps:.1f}bps — short spot/long DBC"
    else:
        legs = {"spot": "LONG", "dbc": "SHORT"}
        rationale = f"carry {carry_ann:.1f}bps basis {basis_bps:.1f}bps — long spot/short DBC"
    return _make_signal(strategy="stock_carry",asset=asset,direction=direction,confidence_bps=int(conf*10000),
                        entry_price=spot,rationale=rationale,
                        metadata={"basis_bps":basis_bps,"carry_bps":carry_ann,"legs":legs})

def vault_9010_allocation(deposit_usd: float, yield_apy: float, T_years: float = 1.0, call_price: float = 1.0):
    """90/10 note: 90% to yield, 10% + yield buys OTM calls. Floor ≈ deposit."""
    safe = 0.9*deposit_usd; opt_budget = 0.1*deposit_usd
    safe_T = safe*(1+yield_apy*T_years)
    n_calls = opt_budget/call_price if call_price>0 else 0
    floor = safe_T  # choose yield_apy*T to cover opt_budget for full protection
    return {"safe_now":safe,"opt_budget":opt_budget,"safe_at_T":safe_T,
            "n_calls":n_calls,"floor":floor,"protected":floor>=deposit_usd*0.99}

def bsm_mispricing(spot, strike, T, r, q, b_borrow, sig_oracle, market_price, kind="call"):
    """Scanner: theoretical (Merton) vs on-chain market. >0 = on-chain rich."""
    from .bsm import bs_price_merton
    theo=bs_price_merton(spot,strike,T,r,q,b_borrow,sig_oracle,kind)
    return {"theoretical":theo,"market":market_price,"edge":market_price-theo}
