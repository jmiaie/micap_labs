#!/usr/bin/env python3
"""Run the live paper-trading loop (NO real orders — signals + ledger only).

Requires exchange/venue API access (run on your machine, not a restricted
sandbox):

    pip install ".[live]"
    python scripts/paper_trade.py --artifacts data/artifacts/live \
        --venues polymarket kalshi --windows 5 15

Stop with Ctrl-C; inspect results with:
    python -c "from btc_edge.live import Ledger; print(Ledger().summary())"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from btc_edge.live.feed import SpotFeed
from btc_edge.live.ledger import Ledger
from btc_edge.live.runner import PaperTrader
from btc_edge.models.pricer import ZDistribution


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", default="data/artifacts/live")
    ap.add_argument("--venues", nargs="+", default=["polymarket", "kalshi"])
    ap.add_argument("--windows", type=int, nargs="+", default=[5, 15])
    ap.add_argument("--poll-seconds", type=float, default=5.0)
    ap.add_argument("--ledger", default="data/ledger/paper.jsonl")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    zdists = {}
    zpath = Path(args.artifacts) / "zdists.json"
    if zpath.exists():
        zdists = {int(k): ZDistribution.from_dict(v)
                  for k, v in json.loads(zpath.read_text()).items()}
        print(f"loaded {len(zdists)} z-distributions")
    else:
        print("WARNING: no zdists.json — pricer falls back to normal tails")

    feed = SpotFeed()
    feed.bootstrap()
    trader = PaperTrader(feed, Ledger(args.ledger), zdists=zdists,
                         windows=tuple(args.windows), poll_seconds=args.poll_seconds)

    poly = kalshi = None
    if "polymarket" in args.venues:
        from btc_edge.markets.polymarket import PolymarketClient
        poly = PolymarketClient()
    if "kalshi" in args.venues:
        from btc_edge.markets.kalshi import KalshiClient
        kalshi = KalshiClient()
    trader.attach_venues(polymarket=poly, kalshi=kalshi)

    async def run():
        feed.start()
        await trader.run()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nstopped.", Ledger(args.ledger).summary())


if __name__ == "__main__":
    main()
