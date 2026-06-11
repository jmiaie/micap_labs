"""Polymarket read-only client (Gamma metadata + CLOB order books).

Targets the recurring short-window crypto series ("Bitcoin Up or Down" style
markets). Series naming/slugs evolve, so discovery filters on title/duration
rather than hardcoded slugs and returns whatever is live. Read-only: order
placement is intentionally out of scope until paper results justify it.

Settlement note: Polymarket's short-window BTC markets resolve on a specific
oracle feed (Chainlink/Pyth per market description, not Binance). The pricer's
spot input should ultimately be the SAME feed the market settles on; using an
exchange feed as proxy adds basis risk that the paper ledger will surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from .edge import Quote

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

UPDOWN_RE = re.compile(r"\b(up or down|up-or-down)\b", re.I)
BTC_RE = re.compile(r"\b(btc|bitcoin)\b", re.I)


@dataclass
class PolyMarket:
    market_id: str
    question: str
    slug: str
    token_yes: str
    token_no: str
    start: datetime | None
    end: datetime | None

    @property
    def window_minutes(self) -> float | None:
        if self.start and self.end:
            return (self.end - self.start).total_seconds() / 60.0
        return None


class PolymarketClient:
    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self.s = session or requests.Session()
        self.timeout = timeout

    def find_btc_updown(self, window_min: float | None = None, limit: int = 200) -> list[PolyMarket]:
        """Live BTC up/down markets, optionally filtered to ~window_min length."""
        r = self.s.get(f"{GAMMA}/markets", params={
            "active": "true", "closed": "false", "limit": limit,
            "order": "endDate", "ascending": "true",
        }, timeout=self.timeout)
        r.raise_for_status()
        out = []
        for m in r.json():
            q = m.get("question") or ""
            if not (BTC_RE.search(q) and UPDOWN_RE.search(q)):
                continue
            toks = m.get("clobTokenIds")
            if isinstance(toks, str):
                import json as _json
                toks = _json.loads(toks)
            if not toks or len(toks) < 2:
                continue
            pm = PolyMarket(
                market_id=str(m.get("id")),
                question=q,
                slug=m.get("slug", ""),
                token_yes=toks[0],
                token_no=toks[1],
                start=_ts(m.get("startDate") or m.get("eventStartTime")),
                end=_ts(m.get("endDate")),
            )
            if window_min and pm.window_minutes and abs(pm.window_minutes - window_min) > window_min * 0.5:
                continue
            out.append(pm)
        return out

    def quote(self, market: PolyMarket) -> Quote | None:
        """Best bid/ask for the YES token from the CLOB book."""
        r = self.s.get(f"{CLOB}/book", params={"token_id": market.token_yes}, timeout=self.timeout)
        r.raise_for_status()
        book = r.json()
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            return None
        return Quote(
            venue="polymarket",
            market_id=market.slug or market.market_id,
            yes_bid=max(float(b["price"]) for b in bids),
            yes_ask=min(float(a["price"]) for a in asks),
            expiry_utc=market.end,
            meta={"question": market.question},
        )


def _ts(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
