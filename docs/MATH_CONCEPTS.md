# Mathematical Concepts for Tarstrade — Provided Spec

*Provided 2026-09-06 — wired to `funding_persistence_z` live and ML carry later. No implementation yet, spec saved for resume.*

---

## 1. Quantile Regression / Pinball Loss — Koenker & Bassett (1978)

**Gate:** `q20 >= 16bps` must hold out-of-sample, with coverage `P(y < q20) ≈ 0.2`

```python
def fit_quantile(X, y, tau, **kw):
    return lgb.LGBMRegressor(objective='quantile', alpha=tau, n_estimators=400, max_depth=4, learning_rate=0.03, subsample=0.8, colsample_bytree=0.8, **kw).fit(X, y)
m20 = fit_quantile(X, y_sum_7d_bps, tau=0.2)
m50 = fit_quantile(X, y_sum_7d_bps, tau=0.5)
q20, q50 = m20.predict(X_new), m50.predict(X_new)
q20 = np.minimum(q20, q50)  # enforce non-crossing
pass_gate = q20 >= 16.0
```

**Tarstrade wiring (later):** Replace `funding_persistence_z` binary `|z|>=1.5` with `q20` quantile gate on `y_sum_7d_bps` with same features + `pin_state`. Current rule stays live until ML resume.

---

## 2. Discrete-Time Survival / Hazard — Prentice & Gloeckler (1978), Singer & Willett ch.10-11

Person-period rows: one per (spell, day-alive), `flipped` 1 on flip day, 0 otherwise.

```python
X_pp = sm.add_constant(pd.get_dummies(df_pp['spell_age']).join(df_pp[feature_cols]))
hazard_model = sm.Logit(df_pp['flipped'], X_pp).fit()  # h_t = P(flip|alive, spell_age)
S = np.cumprod(1 - np.array(h_t_hat))  # S(k)
E_pnl = S[-1] * funding_sum_7d - costs
```

**Tarstrade wiring:** Endogenous horizon for `carry_break_even_rate()` — `S(k)` tells if position survives to day 7 before evaluating costs. Needs `spell_age` feature (already in `dataset.csv`).

---

## 3. Funding Decomposition — clamp spec

```
funding_raw = premium_TWAP + interest
funding = clamp(funding_raw, floor, cap)
pin_state = 1{raw <= floor} - 1{raw >= cap}  # -1/0/+1
premium_z = (premium_TWAP - roll_mean(W)) / roll_std(W)
residual = funding - funding_raw  # clamp bite
```

`pin_state` must be feature for quantile model — else misattributes clamp flatness to signal decay.

**Tarstrade wiring:** Build `decompose_funding()` for ML features when premium archiver has 30d history; currently premium is NaN (archiver parked).

---

## 4-8 Quick Refs (when resume)

**4. EWMA→GARCH(1,1):** `σ²_t = ω+αr²_{t-1}+βσ²_{t-1}` `arch_model(returns, vol='Garch',p=1,q=1)` → `RiskGate.regime_scale` ; Kelly `f*=μ/σ²` (half-Kelly) not `2p-1` for continuous PnL.

**5. Isotonic calibration:** `IsotonicRegression().fit(q_pred, y_actual)` + decile `groupby(qcut(q_pred,10)).y_actual.mean()` — v2 52.5%→-90% signature flat curve.

**6. PBO/CSCV + DSR:** Bailey & López 2014; López AFML ch.11-14 full combinatorial splits + Deflated Sharpe.

**7. Jump/Hawkes:** Merton `dS=μdt+σdW+JdN`; Hawkes `λ(t)=μ+Σκe^{-β(t-t_i)}` for memecoin pump clustering.

**8. Market impact:** Almgren-Chriss orderbook vs AMM `Δy/y=Δx/(x+Δx)` from reserves, MEV scales with `Δx/pool`.

---

**Status:** Saved for ML resume. `funding_persistence_z` SOL live (`cleared_for_paper_trading=True`) continues paper-trade; premium archiver and these 3 concepts parked.
