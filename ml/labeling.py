"""Triple-barrier labeling (AFML ch3) + average-uniqueness weights (ch4).

Labels answer "did a long from this bar's close hit +m*sigma_H or -m*sigma_H
first within H bars?" instead of v1's raw sign(close_{t+4} - close_t). A trade
that survives barriers is a *trade-shaped* outcome, which is what the meta
model needs to learn to filter.

Bar-level honesty rule: if high and low pierce both barriers inside the same
1h bar, the touch order is unknowable -- the event is labeled by that bar's
close (vertical/realized sign), never credited with the favorable barrier.
Barrier exits assume a fill at the barrier price (same optimism class as the
gate run's "1h close executable at 5+3 bps"; documented in the report).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def triple_barrier(df: pd.DataFrame, horizon: int = 24, m: float = 1.0,
                   vol_lookback: int = 24) -> pd.DataFrame:
    """Return events aligned to df rows: outcome (+1/-1), exit_j (bar index of
    exit), ret_long (realized fractional move close[t] -> exit price)."""
    c = df["close"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    lr = np.diff(np.log(c), prepend=np.nan)
    n = len(c)
    sigma = pd.Series(lr).rolling(vol_lookback).std().to_numpy() * np.sqrt(horizon)

    outcome = np.zeros(n)
    exit_j = np.full(n, -1, dtype=np.int64)
    ret_long = np.full(n, np.nan)
    for t in range(vol_lookback, n - horizon):
        s = sigma[t]
        if not np.isfinite(s) or s <= 0:
            continue
        up = c[t] * (1.0 + m * s)
        dn = c[t] * (1.0 - m * s)
        o, xj, r = 0, t + horizon, c[t + horizon] / c[t] - 1.0
        for j in range(t + 1, t + horizon + 1):
            hit_up, hit_dn = h[j] >= up, l[j] <= dn
            if hit_up and hit_dn:      # unknowable order -> realized close
                o, xj, r = int(np.sign(c[j] / c[t] - 1.0) or 1), j, c[j] / c[t] - 1.0
                break
            if hit_up:
                o, xj, r = 1, j, m * s
                break
            if hit_dn:
                o, xj, r = -1, j, -m * s
                break
        outcome[t], exit_j[t], ret_long[t] = o, xj, r
    return pd.DataFrame({"outcome": outcome, "exit_j": exit_j, "ret_long": ret_long})


def uniqueness_weights(t_events: np.ndarray, horizon: int) -> np.ndarray:
    """AFML average uniqueness: 1/(avg #concurrent event windows spanning each
    bar of this event's life). Overlapping 24h labels get down-weighted so the
    same move is not taught to the model three times."""
    tau = np.asarray(t_events)
    last_bar = int(tau[-1] + horizon)
    w = np.ones(len(tau))
    for i, t in enumerate(tau):
        bars = np.arange(int(t), min(int(t) + horizon, last_bar))
        active = (np.searchsorted(tau, bars, side="right")
                  - np.searchsorted(tau, bars - horizon, side="right"))
        w[i] = 1.0 / max(1.0, float(np.mean(np.maximum(active, 1))))
    return w
