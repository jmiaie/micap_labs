"""Synthetic BTC-like 1m bar generator.

Used ONLY for unit tests and offline pipeline development. It is calibrated to
look like BTC (vol clustering, fat tails, intraday seasonality, jumps,
flow/return correlation) and contains a small injected momentum signal so the
pipeline's ability to *find* signal can be tested deterministically.

Never quote performance numbers from synthetic data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def synthetic_bars(
    n_days: int = 30,
    seed: int = 7,
    s0: float = 65_000.0,
    base_vol_per_min: float = 0.0006,   # ~3.1% daily vol
    momentum: float = 0.03,             # AR(1) on 1m returns; small, realistic-ish edge
    jump_prob_per_min: float = 0.0008,
    start: str = "2024-01-01",
) -> pd.DataFrame:
    """Generate a 1m OHLCV frame with `taker_buy_volume`."""
    rng = np.random.default_rng(seed)
    n = n_days * 1440
    idx = pd.date_range(start, periods=n, freq="1min", tz="UTC")

    # --- stochastic vol: AR(1) in log-vol + intraday U-shape (UTC hours) ---
    log_vol = np.empty(n)
    log_vol[0] = 0.0
    phi, vol_of_vol = 0.997, 0.05
    eps = rng.standard_normal(n)
    for t in range(1, n):
        log_vol[t] = phi * log_vol[t - 1] + vol_of_vol * eps[t]
    hours = idx.hour.to_numpy() + idx.minute.to_numpy() / 60
    intraday = 1.0 + 0.35 * np.cos((hours - 15.0) / 24 * 2 * np.pi)  # peak ~US hours
    sigma = base_vol_per_min * np.exp(log_vol) * intraday

    # --- returns: small AR(1) momentum + fat-tailed shocks + jumps ---
    shocks = rng.standard_t(df=4, size=n) / np.sqrt(2.0)  # unit-ish variance, fat tails
    jumps = (rng.random(n) < jump_prob_per_min) * rng.standard_normal(n) * 8.0
    r = np.empty(n)
    r[0] = sigma[0] * shocks[0]
    for t in range(1, n):
        r[t] = momentum * r[t - 1] + sigma[t] * (shocks[t] + jumps[t])

    log_close = np.log(s0) + np.cumsum(r)
    close = np.exp(log_close)
    open_ = np.empty(n)
    open_[0] = s0
    open_[1:] = close[:-1]

    # intrabar extremes from |return| plus noise
    span = np.abs(r) + sigma * np.abs(rng.standard_normal(n)) * 0.8
    high = np.maximum(open_, close) * np.exp(span * rng.random(n) * 0.6)
    low = np.minimum(open_, close) * np.exp(-span * rng.random(n) * 0.6)

    # volume: lognormal, correlated with |r|; taker buy share tracks return sign
    volume = np.exp(rng.normal(0.0, 0.5, n)) * (1.0 + 120.0 * np.abs(r)) * 12.0
    buy_share = 1.0 / (1.0 + np.exp(-(r / np.maximum(sigma, 1e-9)) * 0.9 - rng.normal(0, 0.35, n)))
    taker_buy = volume * np.clip(buy_share, 0.02, 0.98)

    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "taker_buy_volume": taker_buy,
        },
        index=idx,
    )
