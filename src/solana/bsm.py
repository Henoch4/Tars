"""BSM + Merton cost-of-carry (b = r - q - b_borrow). Pure math, no deps beyond stdlib."""
from __future__ import annotations
import math

def N(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def Nprime(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def bs_price(S,K,T,r,sig,kind="call") -> float:
    """Classic no-dividend (for tests / README parity)."""
    if T<=0 or sig<=0 or S<=0 or K<=0:
        return max(S-K,0) if kind=="call" else max(K-S,0)
    d1=(math.log(S/K)+(r+0.5*sig*sig)*T)/(sig*math.sqrt(T)); d2=d1-sig*math.sqrt(T)
    df=math.exp(-r*T)
    return S*N(d1)-K*df*N(d2) if kind=="call" else K*df*N(-d2)-S*N(-d1)

def cost_of_carry(r: float, q: float, b_borrow: float) -> float:
    """Merton continuous carry: b = r - q - b_borrow."""
    return r - q - b_borrow

def bs_price_merton(S,K,T,r,q,b_borrow,sig,kind="call") -> float:
    """Call = S*e^{-(q+b)T}N(d1) - K*e^{-rT}N(d2), d1 uses b."""
    b = cost_of_carry(r,q,b_borrow)
    if T<=0 or sig<=0 or S<=0 or K<=0:
        return max(S-K,0) if kind=="call" else max(K-S,0)
    d1=(math.log(S/K)+(b+0.5*sig*sig)*T)/(sig*math.sqrt(T)); d2=d1-sig*math.sqrt(T)
    return S*math.exp((b-r)*T)*N(d1)-K*math.exp(-r*T)*N(d2) if kind=="call" else K*math.exp(-r*T)*N(-d2)-S*math.exp((b-r)*T)*N(d1)

def delta_merton(S,K,T,r,q,b_borrow,sig,kind="call") -> float:
    b = cost_of_carry(r,q,b_borrow)
    if T<=0 or sig<=0 or S<=0 or K<=0: return 1.0 if (kind=="call" and S>K) else 0.0
    d1=(math.log(S/K)+(b+0.5*sig*sig)*T)/(sig*math.sqrt(T))
    disc=math.exp((b-r)*T)  # = exp(-(q+b_borrow)T)
    return disc*N(d1) if kind=="call" else disc*(N(d1)-1)

def greeks(S,K,T,r,sig):
    d1=(math.log(S/K)+(r+0.5*sig*sig)*T)/(sig*math.sqrt(T)); d2=d1-sig*math.sqrt(T)
    df=math.exp(-r*T); nd1p=Nprime(d1)
    return {"delta_call":N(d1),"delta_put":N(d1)-1,"gamma":nd1p/(S*sig*math.sqrt(T)),
            "vega":S*nd1p*math.sqrt(T),
            "theta_call":-(S*nd1p*sig)/(2*math.sqrt(T))-r*K*df*N(d2),
            "theta_put":-(S*nd1p*sig)/(2*math.sqrt(T))+r*K*df*N(-d2),"d1":d1,"d2":d2}

def greeks_merton(S,K,T,r,q,b_borrow,sig):
    """Greeks with Merton carry: d1/d2 built from b = r - q - b_borrow.
    Delta = e^{(b-r)T}N(d1), gamma/vega scaled by e^{(b-r)T}."""
    if T <= 0 or sig <= 0 or S <= 0 or K <= 0:
        iv = 1.0 if S > K else 0.0
        return {"delta_call": iv, "delta_put": iv - 1, "gamma": 0.0, "vega": 0.0,
                "theta_call": 0.0, "theta_put": 0.0, "d1": float("inf"), "d2": float("inf")}
    b = cost_of_carry(r, q, b_borrow)
    d1 = (math.log(S / K) + (b + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    disc = math.exp((b - r) * T)
    df = math.exp(-r * T)
    nd1p = Nprime(d1)
    return {
        "delta_call": disc * N(d1),
        "delta_put": disc * (N(d1) - 1),
        "gamma": disc * nd1p / (S * sig * math.sqrt(T)),
        "vega": disc * S * nd1p * math.sqrt(T),
        "theta_call": -(disc * S * nd1p * sig) / (2 * math.sqrt(T)) - r * K * df * N(d2),
        "theta_put": -(disc * S * nd1p * sig) / (2 * math.sqrt(T)) + r * K * df * N(-d2),
        "d1": d1,
        "d2": d2,
    }

def implied_vol(price,S,K,T,r,kind="call",init=0.3):
    sig=init
    for _ in range(50):
        p=bs_price(S,K,T,r,sig,kind); v=greeks(S,K,T,r,sig)["vega"]
        if v<1e-8: break
        diff=p-price
        if abs(diff)<1e-6: return sig
        sig=max(0.01,min(5.0,sig-diff/v))
    lo,hi=0.01,5.0
    for _ in range(50):
        mid=(lo+hi)/2
        if bs_price(S,K,T,r,mid,kind)>price: hi=mid
        else: lo=mid
    return (lo+hi)/2

def hedge_order(delta_now, delta_held, notional, band=0.05):
    """Thorp rebalance: trade gap only if |gap|>band. Caller adds 45s persistence."""
    gap=delta_now-delta_held
    if abs(gap)<band: return None
    return {"side":"buy" if gap>0 else "sell","size_usd":abs(gap)*notional,"reason":f"delta_gap={gap:.3f}"}
