"""Live BTC spot state from Binance websocket (bookTicker + 1m klines).

Maintains: current mid, ring buffer of 1m closes, EWMA vol, and per-window
open prints for the 5m/15m wall-clock windows. Bootstraps from REST so vol
and features are warm immediately.

Run on a machine with exchange access (`pip install .[live]`). If your venue
settles on a different feed (Chainlink/Pyth for Polymarket, BRTI for Kalshi),
treat this as a low-latency proxy and monitor basis in the ledger.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
from collections import deque
from datetime import datetime, timezone

from ..models.pricer import EwmaVol

log = logging.getLogger(__name__)

WS_URL = "wss://stream.binance.com:9443/stream?streams={sym}@bookTicker/{sym}@kline_1m"


class SpotFeed:
    def __init__(self, symbol: str = "btcusdt", vol_halflife_min: float = 30.0,
                 history_min: int = 2880):
        self.symbol = symbol
        self.mid: float | None = None
        self.last_update: datetime | None = None
        self.closes: deque[tuple[datetime, float]] = deque(maxlen=history_min)
        self.vol = EwmaVol(halflife_min=vol_halflife_min)
        self.window_opens: dict[int, tuple[datetime, float]] = {}  # window_min -> (start, open)
        self._task: asyncio.Task | None = None

    # ---------------- bootstrap ----------------
    def bootstrap(self) -> None:
        from ..data.binance import fetch_klines
        import pandas as pd

        bars = fetch_klines(self.symbol.upper(), "1m",
                            start=pd.Timestamp.utcnow() - pd.Timedelta(minutes=self.closes.maxlen + 5))
        for ts, row in bars.iterrows():
            self._on_minute_close(ts.to_pydatetime(), float(row["close"]))
        if len(bars):
            self.mid = float(bars["close"].iloc[-1])
        log.info("bootstrapped %d minutes, vol=%.6f", len(bars), self.vol.sigma)

    # ---------------- websocket ----------------
    async def run(self) -> None:
        import websockets

        url = WS_URL.format(sym=self.symbol)
        while True:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    log.info("feed connected")
                    async for raw in ws:
                        self._on_msg(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # reconnect forever
                log.warning("feed error (%s); reconnecting in 2s", e)
                await asyncio.sleep(2)

    def start(self) -> None:
        self._task = asyncio.get_event_loop().create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    # ---------------- handlers ----------------
    def _on_msg(self, msg: dict) -> None:
        stream = msg.get("stream", "")
        data = msg.get("data", {})
        now = datetime.now(timezone.utc)
        if stream.endswith("bookTicker"):
            try:
                self.mid = 0.5 * (float(data["b"]) + float(data["a"]))
                self.last_update = now
                self._roll_windows(now)
            except (KeyError, ValueError):
                pass
        elif stream.endswith("kline_1m"):
            k = data.get("k", {})
            if k.get("x"):  # bar closed
                ts = datetime.fromtimestamp(k["T"] / 1000, tz=timezone.utc)
                self._on_minute_close(ts, float(k["c"]))

    def _on_minute_close(self, ts: datetime, close: float) -> None:
        if self.closes and self.closes[-1][1] > 0:
            self.vol.update(math.log(close / self.closes[-1][1]))
        self.closes.append((ts, close))

    def _roll_windows(self, now: datetime) -> None:
        """Capture window-open prints at 5m/15m wall-clock boundaries."""
        for w in (5, 15):
            start_min = (now.minute // w) * w
            start = now.replace(minute=start_min, second=0, microsecond=0)
            cur = self.window_opens.get(w)
            if (cur is None or cur[0] != start) and self.mid is not None:
                self.window_opens[w] = (start, self.mid)

    # ---------------- views ----------------
    def snapshot(self) -> dict:
        return {
            "mid": self.mid,
            "sigma_per_min": self.vol.sigma,
            "window_opens": {w: (s.isoformat(), p) for w, (s, p) in self.window_opens.items()},
            "minutes_buffered": len(self.closes),
            "last_update": self.last_update.isoformat() if self.last_update else None,
        }
