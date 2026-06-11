"""BTC/USD 1m history from github.com/ff137/bitstamp-btcusd-minute-data.

MIT-licensed Bitstamp candles since 2012, refreshed daily by the publisher's
GitHub Action. Works through GitHub-only network policies (raw.githubusercontent.com),
which exchange APIs typically do not — this is how restricted environments and
CI get real data. No taker-buy volume (Bitstamp doesn't publish it), so flow
features are absent when training on this source.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

REPO_RAW = "https://raw.githubusercontent.com/ff137/bitstamp-btcusd-minute-data/main"
BULK_URL = f"{REPO_RAW}/data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz"
LATEST_URL = f"{REPO_RAW}/data/updates/btcusd_bitstamp_1min_latest.csv"


def fetch_bitstamp_1m(cache_dir: str | Path = "data/bars", refresh: bool = True) -> pd.DataFrame:
    """Download (or refresh) the full 1m history; returns canonical bars and
    caches a merged parquet at <cache_dir>/btc_1m.parquet."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / "btc_1m.parquet"
    bulk_cache = cache_dir / "bitstamp_bulk.csv.gz"

    if not bulk_cache.exists():
        _download(BULK_URL, bulk_cache)
    bulk = pd.read_csv(bulk_cache)

    frames = [bulk]
    if refresh:
        r = requests.get(LATEST_URL, timeout=60)
        r.raise_for_status()
        frames.append(pd.read_csv(io.StringIO(r.text)))

    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = (df.drop_duplicates("timestamp", keep="last")
            .set_index("timestamp").sort_index()
            [["open", "high", "low", "close", "volume"]].astype(float))
    df.reset_index().to_parquet(cache, index=False)
    return df


def _download(url: str, dest: Path, chunk: int = 1 << 20) -> None:
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for part in r.iter_content(chunk_size=chunk):
                f.write(part)
