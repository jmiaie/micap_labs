"""Binance klines fetcher (run on a machine with exchange API access).

Binance 1m klines include `taker_buy_volume` (field 9), the order-flow
imbalance proxy used by the flow features — prefer this source for live
training data. No API key required for market data.
"""

from __future__ import annotations

import time

import pandas as pd
import requests

BASE = "https://api.binance.com/api/v3/klines"
DATA_BASE = "https://data-api.binance.vision/api/v3/klines"  # key-less mirror

COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "n_trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    base_url: str = DATA_BASE,
    pause_s: float = 0.15,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Paginated kline download, returns canonical bar schema."""
    s = session or requests.Session()
    start_ms = int(pd.Timestamp(start or "2020-01-01", tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000) if end else int(time.time() * 1000)

    frames = []
    cur = start_ms
    while cur < end_ms:
        r = s.get(base_url, params={
            "symbol": symbol, "interval": interval,
            "startTime": cur, "endTime": end_ms, "limit": 1000,
        }, timeout=20)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        frames.append(pd.DataFrame(rows, columns=COLS))
        cur = rows[-1][0] + 60_000
        time.sleep(pause_s)

    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    idx = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    out = pd.DataFrame({
        "open": df["open"].astype(float).to_numpy(),
        "high": df["high"].astype(float).to_numpy(),
        "low": df["low"].astype(float).to_numpy(),
        "close": df["close"].astype(float).to_numpy(),
        "volume": df["volume"].astype(float).to_numpy(),
        "taker_buy_volume": df["taker_buy_volume"].astype(float).to_numpy(),
        "n_trades": df["n_trades"].astype(int).to_numpy(),
    }, index=idx)
    return out[~out.index.duplicated(keep="last")].sort_index()
