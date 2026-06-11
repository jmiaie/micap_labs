"""Forward-looking labels. The ONLY module allowed to look into the future."""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_labels(bars: pd.DataFrame, horizons_min=(5, 15)) -> pd.DataFrame:
    """For each horizon h, build:
      up_{h}m      — 1 if close[t+h] > close[t] else 0 (ties -> 0; both venues
                     settle ties to the 'down/no' side or void; configurable later)
      fwd_ret_{h}m — log forward return, for diagnostics and EV analysis

    Rows in the final `h` minutes have NaN labels and must be dropped before
    training. Bars are assumed to be on a 1-minute grid (validate_bars).
    """
    c = bars["close"].astype(float)
    logc = np.log(c)
    out = pd.DataFrame(index=bars.index)
    for h in horizons_min:
        fwd = logc.shift(-h) - logc
        out[f"fwd_ret_{h}m"] = fwd
        out[f"up_{h}m"] = (fwd > 0).astype(float).where(fwd.notna())
    return out


def aligned_xy(
    features: pd.DataFrame, labels: pd.DataFrame, horizon_min: int
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Join features with one horizon's label, drop unusable rows.

    Returns (X, y, fwd_ret) with identical indexes.
    """
    y = labels[f"up_{horizon_min}m"]
    fwd = labels[f"fwd_ret_{horizon_min}m"]
    df = features.join(y.rename("y"), how="inner").join(fwd.rename("fwd"), how="inner")
    df = df.dropna()
    return df[features.columns], df["y"].astype(int), df["fwd"]
