"""Causal feature engineering on 1m bars.

Every feature at timestamp t uses ONLY bars with index <= t (trailing windows,
EWMs, shifts). The no-lookahead property is enforced by tests/test_features.py
which mutates future bars and asserts feature rows at t are unchanged.

Feature families:
  momentum   — multi-scale log returns and vol-normalized momentum z-scores
  volatility — EWMA realized vol, Parkinson range vol, vol-regime ratio
  position   — price location vs rolling range / rolling VWAP, candle anatomy
  flow       — taker-buy imbalance (order-flow proxy from Binance kline field)
  seasonality— minute-of-hour / hour-of-day / day-of-week encodings, plus
               proximity to round-number price levels
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# rows to drop at the head so all rolling windows are warm
FEATURE_WARMUP_MIN = 24 * 60

RET_WINDOWS = (1, 3, 5, 15, 30, 60, 120)
VOL_HALFLIVES = (10, 30, 120)
IMB_WINDOWS = (5, 15, 60)


def build_features(bars: pd.DataFrame, drop_warmup: bool = True) -> pd.DataFrame:
    """Return the feature matrix (float32) aligned to `bars.index`."""
    c = bars["close"].astype(float)
    h = bars["high"].astype(float)
    l = bars["low"].astype(float)
    o = bars["open"].astype(float)
    v = bars["volume"].astype(float)
    logc = np.log(c)
    r1 = logc.diff()

    f = pd.DataFrame(index=bars.index)

    # --- momentum ---
    for w in RET_WINDOWS:
        f[f"ret_{w}m"] = logc.diff(w)

    # --- volatility (per-minute units) ---
    for hl in VOL_HALFLIVES:
        f[f"ewma_vol_{hl}m"] = r1.ewm(halflife=hl, min_periods=hl).std()
    base_vol = f["ewma_vol_30m"].clip(lower=1e-6)
    for w in (5, 15, 30, 60):
        f[f"mom_z_{w}m"] = f[f"ret_{w}m"] / (base_vol * np.sqrt(w))
    with np.errstate(divide="ignore", invalid="ignore"):
        park = (np.log(h / l) ** 2) / (4.0 * np.log(2.0))
    f["park_vol_30m"] = np.sqrt(park.rolling(30, min_periods=15).mean())
    f["vol_regime"] = f["ewma_vol_10m"] / f["ewma_vol_120m"].clip(lower=1e-6)
    # squared-return autocorr proxy: short vs long realized vol on 5m grid
    f["rv_ratio_5_60"] = (
        r1.pow(2).rolling(5).sum().div(r1.pow(2).rolling(60).sum().clip(lower=1e-12))
    )

    # --- price position ---
    for w in (30, 120):
        roll_max = c.rolling(w, min_periods=w // 2).max()
        roll_min = c.rolling(w, min_periods=w // 2).min()
        f[f"range_pos_{w}m"] = ((c - roll_min) / (roll_max - roll_min).clip(lower=1e-9)).clip(0, 1)
    pv = (c * v).rolling(60, min_periods=30).sum()
    vv = v.rolling(60, min_periods=30).sum().clip(lower=1e-9)
    f["vwap_dev_60m"] = np.log(c / (pv / vv))
    rng_bar = (h - l).clip(lower=1e-9)
    f["body_ratio"] = (c - o) / rng_bar
    f["upper_wick"] = (h - np.maximum(o, c)) / rng_bar
    f["lower_wick"] = (np.minimum(o, c) - l) / rng_bar

    # --- flow ---
    vol_mean = v.rolling(120, min_periods=60).mean()
    vol_std = v.rolling(120, min_periods=60).std().clip(lower=1e-9)
    f["volume_z"] = ((v - vol_mean) / vol_std).clip(-10, 10)
    if "taker_buy_volume" in bars.columns:
        signed = 2.0 * bars["taker_buy_volume"].astype(float) - v  # buy - sell volume
        for w in IMB_WINDOWS:
            f[f"flow_imb_{w}m"] = (
                signed.rolling(w, min_periods=w).sum() / v.rolling(w, min_periods=w).sum().clip(lower=1e-9)
            )
        # CVD slope: change of cumulative signed volume, vol-of-volume normalized
        cvd = signed.cumsum()
        f["cvd_slope_15m"] = (cvd - cvd.shift(15)) / v.rolling(60, min_periods=30).sum().clip(lower=1e-9)

    # --- classic oscillators (QuantAgent's indicator set, pandas-native) ---
    # RSI(14), Wilder smoothing, centered to [-1, 1]
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
    rsi = 100 - 100 / (1 + gain / loss.clip(lower=1e-12))
    f["rsi_14"] = (rsi - 50.0) / 50.0
    # MACD(12,26,9) as fractions of price (scale-free across regimes)
    ema12 = c.ewm(span=12, min_periods=12).mean()
    ema26 = c.ewm(span=26, min_periods=26).mean()
    macd = ema12 - ema26
    f["macd_rel"] = macd / c
    f["macd_hist_rel"] = (macd - macd.ewm(span=9, min_periods=9).mean()) / c
    # Stochastic %K/%D (14,3,3) on high/low extremes, centered
    hh = h.rolling(14, min_periods=14).max()
    ll = l.rolling(14, min_periods=14).min()
    k_raw = (c - ll) / (hh - ll).clip(lower=1e-9)
    f["stoch_k_14"] = k_raw.rolling(3, min_periods=3).mean() - 0.5
    f["stoch_d_14"] = k_raw.rolling(3, min_periods=3).mean().rolling(3, min_periods=3).mean() - 0.5
    # Williams %R at a longer window for scale diversity (28 bars); note the
    # 14-bar version is an affine twin of raw %K, and ROC(k) duplicates ret_km
    hh28 = h.rolling(28, min_periods=28).max()
    ll28 = l.rolling(28, min_periods=28).min()
    f["willr_28"] = (hh28 - c) / (hh28 - ll28).clip(lower=1e-9) - 0.5

    # --- seasonality & levels ---
    minute = f.index.minute.to_numpy()
    hour = f.index.hour.to_numpy() + minute / 60.0
    dow = f.index.dayofweek.to_numpy()
    f["min_sin"] = np.sin(2 * np.pi * minute / 60)
    f["min_cos"] = np.cos(2 * np.pi * minute / 60)
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    f["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    f["dow_cos"] = np.cos(2 * np.pi * dow / 7)
    for level in (1_000.0,):
        dist = (c / level) - np.round(c / level)  # in [-0.5, 0.5], signed
        f["dist_round_1k"] = dist / (base_vol * np.sqrt(15) * c / level).clip(lower=1e-9) / 100.0

    f = f.replace([np.inf, -np.inf], np.nan)
    if drop_warmup and len(f) > FEATURE_WARMUP_MIN:
        f = f.iloc[FEATURE_WARMUP_MIN:]
    return f.astype(np.float32)
