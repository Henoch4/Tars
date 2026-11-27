# EQUATIONS.md — Synthesized refs (your inputs locked in)

## 0. Decisions locked (your answers)

- Stocks: AAPLx, TSLAx, NVDAx. Quote: USDC. Risk: $100 max, $20 daily loss, 1x leverage devnet.
- HMM: 4 features `[vol, funding_z, basis, ret]`, 3 states. Optimal as you said.
- Vault demo: **90/10 Principal-Protected** (not covered call). 90% low-risk yield + 10% yield buys OTM calls on stock basket. 100% protection + asymmetric upside. Covered call deferred (caps upside, harder pitch).
- Wallet: generate throwaway devnet for now. RPC default devnet. Clawpump key Day 4.

## 1. Merton cost-of-carry with borrow (stock_carry.py)

```
b = r - q - b_borrow
r = SOL/USDC risk-free, q = dividend/staking yield, b_borrow = Kamino/Solend locate rate
d1 = [ln(S/K) + (b + σ²/2)T] / (σ√T), d2 = d1 - σ√T
Call = S·e^{(b-r)T}N(d1) - K·e^{-rT}N(d2) = S·e^{-(q+b_borrow)T}N(d1) - K·e^{-rT}N(d2)
Δ_call = e^{(b-r)T}N(d1) = e^{-(q+b_borrow)T}N(d1)
```
Factor `e^{-(q+b_borrow)T}` = spot decay from outgoing yield + borrow fees. Implemented in `bsm.py::bs_price_merton()` + `delta_merton()`.

Carry for signal:
```
Carry_ann = (q + borrow_rebate? actually div_yield + borrow_rate_collected - funding_paid - fee_drag)
Basis_bps = (perp_or_dbc - spot)/spot*10000
LONG spot/SHORT DBC when carry>50bps AND basis>5bps
```

## 2. Thorp → BSM lineage (video sources, no raw LaTeX in transcript)

- Bachelier: fair-bet, pure random, no drift → null model.
- Thorp c.1967: random + drift; Δ=ΔOpt/ΔStock; π=-V+ΔS; $1 up = $1 loss opt + $1 gain stock; sell OTM; buy cheap/short rich.
- BSM 1973 + Merton stochastic calc: hedged→earns r; PDE below; explicit formula.
- Papers: BS73 Princeton PDF, Merton73 MIT, Merton Nobel97, Cornell Medallion20 (PDF + blog + ResearchGate).

## 3. Merton stochastic framework (your synthesis)

SDE: `dV = [αV - D1(V,t)]dt + σV dZ` (α=exp return, D1=div flow, σ=vol, dZ=Wiener)
General PDE: `0.5σ²V²F_VV + [rV-D1]F_V - rF + F_t + D2 = 0` (D2=cash distribution)
European call (D1=0, const σ, strike L, expiry T): `C=V·N(d)-L·e^{-r(T-t)}N(d-σ√(T-t))`, `d=[ln(V/L)+(r+σ²/2)(T-t)]/(σ√(T-t))`

## 4. HMM (Baum→Simons) spec locked

- n=3: 0 low/range (provide LP on DBC), 1 mid/trend (active delta rebalance), 2 high/cascade (pull LP / buy puts).
- Features: vol (7d real/IV), funding_z (perp leverage), basis (spot-vs-synth), ret (log returns).
- Medallion grounding: $100→$398.7M (63.3%), μ=66.1% σ=31.7% Sharpe>2, β≈-1 (SMB/HML neg), 50.75% x millions trades. Lesson: small hedged edge + costs, not prediction. Our vault copies: 16bps hurdle, staircase slippage, thousands small harvests.

## 5. 90/10 Principal-Protected payoff (vault demo)

```
Deposit D (USDC). 0.9D → yield vault (lending, r_y). 0.1D + yield → OTM calls on basket.
Payoff_T = 0.9D·(1+r_y·T) + N_calls·max(S_T-K,0)
Floor = 0.9D·(1+r_y·T) ≈ D (choose r_y·T to cover 0.1D cost) → 100% protection + upside.
```
No liquidation (no margin). Implemented as `vault_9010_allocation()` in stock_carry.py + Anchor vault `package_open` guard.

No other files need your refs. Build proceeds in parallel: bsm.py + stock_carry.py + regime_hmm.py independent (pure math, no shared state).
