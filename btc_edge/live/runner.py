"""Live paper-trading loop.

Every poll cycle:
  1. read spot/vol/window state from the feed
  2. fair value for each live market:
       - mid-window up/down  -> digital pricer anchored at the window open
       - strike markets      -> digital pricer vs strike
       - at window open      -> ML ensemble P(up) if a model artifact is loaded
     blended by time-remaining (ML at open, pricer dominates as tau -> 0)
  3. compare against venue quotes through EdgeEngine; record signals
  4. settle expired windows from the feed (PROXY for the venue oracle —
     realized basis shows up in the ledger summary)

Paper only by design. No order placement exists in this codebase.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ..config import KALSHI_FEES, POLYMARKET_FEES, SizingConfig
from ..markets.edge import EdgeEngine, Quote
from ..models.pricer import ZDistribution, prob_above
from .feed import SpotFeed
from .ledger import Ledger

log = logging.getLogger(__name__)


class PaperTrader:
    def __init__(
        self,
        feed: SpotFeed,
        ledger: Ledger,
        zdists: dict[int, ZDistribution] | None = None,
        ml_model=None,                  # object with predict_proba_up(features_df) or None
        windows=(5, 15),
        poll_seconds: float = 5.0,
        sizing: SizingConfig = SizingConfig(),
    ):
        self.feed = feed
        self.ledger = ledger
        self.zdists = zdists or {}
        self.ml_model = ml_model
        self.windows = windows
        self.poll_seconds = poll_seconds
        self.engines = {
            "polymarket": EdgeEngine(POLYMARKET_FEES, sizing),
            "kalshi": EdgeEngine(KALSHI_FEES, sizing),
        }
        self._poly = None
        self._kalshi = None
        self._open_positions: set[str] = set()
        self._pending_settle: dict[str, tuple[datetime, float]] = {}  # market -> (window_end, open_price)

    def attach_venues(self, polymarket=None, kalshi=None) -> None:
        self._poly = polymarket
        self._kalshi = kalshi

    # ------------------------------------------------------------------
    def fair_up(self, window_min: int) -> tuple[float, float] | None:
        """(fair P(up), minutes_left) for the active window, or None if cold."""
        snap = self.feed.window_opens.get(window_min)
        if snap is None or self.feed.mid is None or self.feed.vol.sigma != self.feed.vol.sigma:
            return None
        start, open_price = snap
        now = datetime.now(timezone.utc)
        tau = window_min - (now - start).total_seconds() / 60.0
        if tau <= 0:
            return None
        zd = self.zdists.get(max(1, round(tau)), ZDistribution())
        p_pricer = prob_above(self.feed.mid, open_price, self.feed.vol.sigma, tau, zd)
        p = p_pricer
        if self.ml_model is not None and tau > window_min * 0.6:
            try:
                p_ml = float(self.ml_model.live_proba_up(self.feed, window_min))
                w = tau / window_min  # ML at the open, pricer as tau decays
                p = w * p_ml + (1 - w) * p_pricer
            except Exception as e:
                log.warning("ml model failed, using pricer only: %s", e)
        return float(p), tau

    # ------------------------------------------------------------------
    async def run(self) -> None:
        while True:
            try:
                self._poll_once()
            except Exception as e:
                log.warning("poll error: %s", e)
            await asyncio.sleep(self.poll_seconds)

    def _poll_once(self) -> None:
        self._settle_expired()
        for w in self.windows:
            fair = self.fair_up(w)
            if fair is None:
                continue
            p_up, tau = fair
            for quote in self._venue_quotes(window_min=w):
                if quote.market_id in self._open_positions:
                    continue
                engine = self.engines[quote.venue]
                sig = engine.evaluate(p_up, quote, meta={
                    "window_min": w, "tau_min": round(tau, 2),
                    "spot": self.feed.mid, "sigma": self.feed.vol.sigma,
                })
                if sig:
                    self.ledger.record(sig)
                    self._open_positions.add(quote.market_id)
                    snap = self.feed.window_opens.get(w)
                    if snap:
                        end = snap[0] + timedelta(minutes=w)
                        self._pending_settle[quote.market_id] = (end, snap[1])
                    log.info("SIGNAL %s %s p=%.3f entry=%.3f edge=%.3f stake=%.4f",
                             sig.venue, sig.side, sig.model_p, sig.entry_price,
                             sig.edge_net, sig.stake_frac)

    def _venue_quotes(self, window_min: int) -> list[Quote]:
        quotes: list[Quote] = []
        if self._poly is not None:
            try:
                for mkt in self._poly.find_btc_updown(window_min=window_min):
                    q = self._poly.quote(mkt)
                    if q:
                        quotes.append(q)
            except Exception as e:
                log.debug("polymarket poll failed: %s", e)
        if self._kalshi is not None:
            try:
                for m in self._kalshi.open_markets():
                    quotes.append(self._kalshi.quote(m))
            except Exception as e:
                log.debug("kalshi poll failed: %s", e)
        return quotes

    def _settle_expired(self) -> None:
        """Settle up/down windows past expiry using the last 1m close at or
        before the window end (feed proxy for the venue oracle)."""
        now = datetime.now(timezone.utc)
        done = []
        for market_id, (end, open_price) in self._pending_settle.items():
            if now < end + timedelta(seconds=75):  # wait for the boundary bar to close
                continue
            settle_px = None
            for ts, close in reversed(self.feed.closes):
                if ts <= end:
                    settle_px = close
                    break
            if settle_px is None:
                continue
            self.ledger.settle(market_id, settle_px > open_price,
                               settle_price_source="binance_1m_close_proxy")
            self._open_positions.discard(market_id)
            done.append(market_id)
        for m in done:
            self._pending_settle.pop(m, None)
