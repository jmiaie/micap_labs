"""Canonical bar schema.

Everything downstream assumes a DataFrame with a tz-aware UTC DatetimeIndex at
a fixed frequency and these columns. `taker_buy_volume` (Binance kline field 9)
is optional but valuable: it gives an order-flow imbalance proxy without
needing the raw trade stream.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BAR_COLUMNS = ["open", "high", "low", "close", "volume"]
OPTIONAL_COLUMNS = ["taker_buy_volume", "n_trades", "quote_volume"]


def validate_bars(df: pd.DataFrame, freq_minutes: int = 1, repair: bool = True) -> pd.DataFrame:
    """Validate and normalize a bar DataFrame.

    - enforces UTC DatetimeIndex, sorted, de-duplicated
    - checks required columns and OHLC sanity
    - if repair=True, reindexes to the full regular grid and forward-fills
      *prices only* across small gaps (volume filled with 0), so rolling
      windows stay aligned. Gaps are common in exchange dumps.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("bars must be indexed by a DatetimeIndex")
    out = df.copy()
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")
    out = out[~out.index.duplicated(keep="last")].sort_index()

    missing = [c for c in BAR_COLUMNS if c not in out.columns]
    if missing:
        raise ValueError(f"bars missing required columns: {missing}")

    bad = (
        (out["high"] < out[["open", "close", "low"]].max(axis=1))
        | (out["low"] > out[["open", "close", "high"]].min(axis=1))
        | (out["close"] <= 0)
    )
    if bad.any():
        out = out[~bad]

    if repair:
        grid = pd.date_range(out.index[0], out.index[-1], freq=f"{freq_minutes}min", tz="UTC")
        out = out.reindex(grid)
        out["gap_filled"] = out["close"].isna()
        out["close"] = out["close"].ffill()
        for c in ("open", "high", "low"):
            out[c] = out[c].fillna(out["close"])
        for c in ("volume", *[c for c in OPTIONAL_COLUMNS if c in out.columns]):
            out[c] = out[c].fillna(0.0)
        out = out.dropna(subset=["close"])  # leading gap before first price
    return out


def gap_report(df: pd.DataFrame, freq_minutes: int = 1) -> dict:
    deltas = df.index.to_series().diff().dt.total_seconds().div(60).dropna()
    return {
        "rows": len(df),
        "start": str(df.index[0]),
        "end": str(df.index[-1]),
        "median_delta_min": float(deltas.median()) if len(deltas) else np.nan,
        "gaps_gt_5min": int((deltas > 5).sum()),
        "max_gap_min": float(deltas.max()) if len(deltas) else np.nan,
        "filled_frac": float(df["gap_filled"].mean()) if "gap_filled" in df else 0.0,
    }


def resample_bars(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Downsample 1m bars to N-minute bars (right-closed, right-labeled so a
    bar stamped t contains information up to and including t)."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    for c in OPTIONAL_COLUMNS:
        if c in df.columns:
            agg[c] = "sum"
    out = df.resample(f"{minutes}min", label="right", closed="right").agg(agg)
    return out.dropna(subset=["close"])
