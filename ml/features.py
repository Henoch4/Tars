"""Feature engineering v2: qlib Alpha158 recipe adapted to 1h crypto bars.

All features are scale-free (ratios/normalized) so a model trained on one
vol regime is not silently rebased by price level. Windows [12, 24, 72, 168]
= half-day / day / 3-day / week, replacing qlib's [5, 10, 20, 30, 60] days.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOWS = [12, 24, 72, 168]
EPS = 1e-12


def load_symbol(repo, sym: str) -> pd.DataFrame:
    """Candles + funding aligned bar-wise (funding forward-filled by timestamp,
    same convention as scripts/ml_spike.py)."""
    df = pd.read_csv(repo / "data" / f"{sym}_1h_candles.csv")
    fr = pd.read_csv(repo / "data" / f"{sym}_funding_binance.csv")
    df["ts"] = df["ts"].astype(np.int64)
    fr["ts"] = fr["ts"].astype(np.int64)
    idx = np.searchsorted(fr["ts"].to_numpy(), df["ts"].to_numpy(), side="right") - 1
    df["funding"] = np.where(idx >= 0, fr["fundingRate"].to_numpy()[np.maximum(idx, 0)], 0.0)
    return df


def _rolling_regression(close: pd.Series, w: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Per-window OLS of close on time -> (slope/close, r^2, last-residual/close).
    Vectorized via sliding windows (qlib BETA / RSQR / RESI)."""
    y = close.to_numpy(dtype=float)
    sw = np.lib.stride_tricks.sliding_window_view(y, w)          # (n-w+1, w)
    t = np.arange(w, dtype=float)
    tc = t - t.mean()
    yc = sw - sw.mean(axis=1, keepdims=True)
    s_tt = (tc * tc).sum()
    slope = (yc * tc).sum(axis=1) / s_tt
    r = (yc * tc).sum(axis=1) / np.sqrt(s_tt * (yc * yc).sum(axis=1) + EPS)
    fitted_last = sw.mean(axis=1) + slope * tc[-1]
    pad = np.full(w - 1, np.nan)
    n = len(y)
    slope_s = pd.Series(np.concatenate([pad, slope / y[w - 1:]]), index=close.index)
    rsqr_s = pd.Series(np.concatenate([pad, r * r]), index=close.index)
    resi_s = pd.Series(np.concatenate([pad, (y[w - 1:] - fitted_last) / y[w - 1:]]), index=close.index)
    return slope_s, rsqr_s, resi_s


def build_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["vol"]
    rng = h - l + EPS
    f = pd.DataFrame(index=df.index)
    # --- kbar: intrabar shape, qlib Alpha158 "kbar" block ---
    f["kmid"] = (c - o) / o
    f["klen"] = (h - l) / o
    f["kmid2"] = (c - o) / rng
    f["kup"] = (h - np.maximum(o, c)) / o
    f["kup2"] = (h - np.maximum(o, c)) / rng
    f["klow"] = (np.minimum(o, c) - l) / o
    f["klow2"] = (np.minimum(o, c) - l) / rng
    f["ksft"] = (2 * c - h - l) / o
    f["ksft2"] = (2 * c - h - l) / rng
    ret1 = np.log(c).diff()
    for w in WINDOWS:
        # --- price rolling block (qlib rolling operators) ---
        f[f"roc_{w}"] = c / c.shift(w) - 1.0
        f[f"ma_{w}"] = c.rolling(w).mean() / c
        f[f"std_{w}"] = c.rolling(w).std() / c
        beta, rsqr, resi = _rolling_regression(c, w)
        f[f"beta_{w}"], f[f"rsqr_{w}"], f[f"resi_{w}"] = beta, rsqr, resi
        f[f"max_{w}"] = h.rolling(w).max() / c
        f[f"low_{w}"] = l.rolling(w).min() / c
        f[f"qtlu_{w}"] = c.rolling(w).quantile(0.8) / c
        f[f"qtld_{w}"] = c.rolling(w).quantile(0.2) / c
        f[f"rank_{w}"] = c.rolling(w).rank(pct=True)
        f[f"corr_{w}"] = c.rolling(w).corr(v)
        # --- return-composition block (qlib CNTP/CNTN/SUMP/SUMN) ---
        pos = ret1.where(ret1 > 0, 0.0)
        neg = (-ret1).where(ret1 < 0, 0.0)
        f[f"cntp_{w}"] = (ret1 > 0).rolling(w).mean()
        f[f"cntn_{w}"] = (ret1 < 0).rolling(w).mean()
        abs_sum = (ret1.abs()).rolling(w).sum()
        f[f"sump_{w}"] = pos.rolling(w).sum() / (abs_sum + EPS)
        f[f"sumn_{w}"] = neg.rolling(w).sum() / (abs_sum + EPS)
        # --- volume block ---
        f[f"vma_{w}"] = v.rolling(w).mean() / (v + EPS)
        f[f"vstd_{w}"] = v.rolling(w).std() / (v + EPS)
    # --- funding block ---
    f["funding"] = df["funding"]
    f["funding_ma24h"] = df["funding"].rolling(3).mean()
    f["funding_ma7d"] = df["funding"].rolling(21).mean()
    mu, sd = df["funding"].rolling(720).mean(), df["funding"].rolling(720).std()
    f["funding_z30d"] = (df["funding"] - mu) / (sd + EPS)
    # --- time ---
    hours = (df["ts"].to_numpy() // 3_600_000) % 24
    f["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hours / 24)
    return f, list(f.columns)
