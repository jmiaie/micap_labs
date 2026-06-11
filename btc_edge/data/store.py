"""Bar file IO."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_bars(path: str | Path) -> pd.DataFrame:
    """Load bars from parquet/csv; accepts either a DatetimeIndex or a
    `timestamp` column (epoch seconds or ISO strings)."""
    p = Path(path)
    df = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
    if "timestamp" in df.columns:
        col = df["timestamp"]
        ts = pd.to_datetime(col, utc=True, unit="s" if col.dtype.kind in "iuf" else None)
        df = df.drop(columns=["timestamp"]).set_index(ts)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError(f"{path}: no DatetimeIndex or timestamp column")
    return df


def save_bars(df: pd.DataFrame, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.reset_index(names="timestamp").to_parquet(path, index=False)
