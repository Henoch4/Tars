"""HMM regime: 3 states x 4 features [vol, funding_z, basis, ret]. hmmlearn if available, else rule fallback."""
from __future__ import annotations

STATES={0:"low/range: provide LP on DBC",1:"mid/trend: active delta rebalance",2:"high/cascade: pull LP / buy puts"}
FEATURES=["vol","funding_z","basis","ret"]

def infer_regime_simple(vol,funding_z,basis,ret) -> int:
    """Deterministic fallback (demo-safe, no training). Thresholds tuned for stocks (tamer than crypto)."""
    if vol>0.6 or abs(funding_z)>2.5 or abs(basis)>150: return 2
    if vol>0.3 or abs(funding_z)>1.2 or abs(basis)>40 or abs(ret)>0.03: return 1
    return 0

class RegimeHMM:
    def __init__(self,n_states=3):
        self.n=n_states; self.model=None
        try:
            from hmmlearn.hmm import GaussianHMM
            self.model=GaussianHMM(n_components=n_states,covariance_type="diag",n_iter=100,random_state=7)
        except Exception: self.model=None
    def fit(self,X):
        if self.model is None: return False
        self.model.fit(X); return True
    def predict(self,x):
        if self.model is None:
            return infer_regime_simple(x[0],x[1],x[2],x[3])
        import numpy as np
        return int(self.model.predict(np.asarray(x).reshape(1,-1))[0])

def dbc_action_for_regime(state: int) -> dict:
    return {0:{"curve":"linear","fee_mult":1.0,"lp":"provide"},
            1:{"curve":"linear->exp","fee_mult":1.2,"lp":"rebalance"},
            2:{"curve":"exponential","fee_mult":2.0,"lp":"pull/hedge"}}[state]
