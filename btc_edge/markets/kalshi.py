"""Kalshi public market-data client (no auth needed for quotes).

BTC series tickers (subject to Kalshi renames — discover, don't assume):
  KXBTC   — hourly "Bitcoin price at X" above/below strikes
  KXBTCD  — daily close markets
Newer sub-hourly BTC series can be found via `search_series("BTC")`.

Settlement: Kalshi BTC markets settle on the CF Benchmarks BRTI index
(Bitstamp is a constituent exchange). Quotes are in cents (1-99).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from .edge import Quote

BASE = "https://api.elections.kalshi.com/trade-api/v2"


@dataclass
class KalshiMarket:
    ticker: str
    title: str
    strike: float | None
    close_time: datetime | None
    yes_bid: float
    yes_ask: float
    volume: int


class KalshiClient:
    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self.s = session or requests.Session()
        self.timeout = timeout

    def search_series(self, keyword: str = "BTC", category: str | None = None) -> list[dict]:
        r = self.s.get(f"{BASE}/series", params={"category": category} if category else {},
                       timeout=self.timeout)
        r.raise_for_status()
        series = r.json().get("series", [])
        kw = keyword.lower()
        return [s for s in series
                if kw in (s.get("ticker", "") + " " + s.get("title", "")).lower()]

    def open_markets(self, series_ticker: str = "KXBTC", limit: int = 100) -> list[KalshiMarket]:
        r = self.s.get(f"{BASE}/markets", params={
            "series_ticker": series_ticker, "status": "open", "limit": limit,
        }, timeout=self.timeout)
        r.raise_for_status()
        out = []
        for m in r.json().get("markets", []):
            out.append(KalshiMarket(
                ticker=m["ticker"],
                title=m.get("title", ""),
                strike=_strike(m),
                close_time=_ts(m.get("close_time")),
                yes_bid=float(m.get("yes_bid", 0)) / 100.0,
                yes_ask=float(m.get("yes_ask", 100)) / 100.0,
                volume=int(m.get("volume", 0)),
            ))
        return out

    def quote(self, m: KalshiMarket) -> Quote:
        return Quote(
            venue="kalshi",
            market_id=m.ticker,
            yes_bid=m.yes_bid,
            yes_ask=m.yes_ask,
            expiry_utc=m.close_time,
            meta={"title": m.title, "strike": m.strike},
        )


def _strike(m: dict) -> float | None:
    for k in ("floor_strike", "cap_strike", "strike"):
        if m.get(k) is not None:
            try:
                return float(m[k])
            except (TypeError, ValueError):
                pass
    return None


def _ts(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
