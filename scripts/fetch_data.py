#!/usr/bin/env python3
"""Fetch BTC 1m history into data/bars/btc_1m.parquet.

Sources:
  --source github   Bitstamp dump via raw.githubusercontent.com (works behind
                    GitHub-only network policies; no taker-buy volume)
  --source binance  Binance REST klines (needs exchange API access; includes
                    taker_buy_volume for the flow features)
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["github", "binance"], default="github")
    ap.add_argument("--start", default="2023-01-01", help="binance only")
    ap.add_argument("--out-dir", default="data/bars")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.source == "github":
        from btc_edge.data.github_bitstamp import fetch_bitstamp_1m

        df = fetch_bitstamp_1m(cache_dir=out_dir)
    else:
        from btc_edge.data.binance import fetch_klines

        df = fetch_klines("BTCUSDT", "1m", start=args.start)
        df.reset_index(names="timestamp").to_parquet(out_dir / "btc_1m.parquet", index=False)

    print(f"{len(df):,} rows  {df.index[0]} -> {df.index[-1]}")
    print(f"wrote {out_dir / 'btc_1m.parquet'}")


if __name__ == "__main__":
    main()
