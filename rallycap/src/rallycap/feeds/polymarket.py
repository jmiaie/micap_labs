"""Polymarket adapters: Gamma API for market discovery, CLOB API for books.

Read-only market data only — order placement lives in execution/broker.py.
`httpx` is imported lazily so the strategy/test stack never needs it.

Endpoints (verify against current docs at M1 — they do evolve):
  - Gamma:  https://gamma-api.polymarket.com/events?...   (market discovery)
  - CLOB:   https://clob.polymarket.com/book?token_id=... (order book)
  - WS:     wss://ws-subscriptions-clob.polymarket.com/ws/market (M1 upgrade)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, List, Optional

from ..types import BookTop, Market

log = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"


def _client():
    import httpx  # lazy: only needed when actually talking to the venue

    return httpx.Client(timeout=10.0, headers={"User-Agent": "rallycap/0.1"})


class GammaClient:
    """Market discovery: find today's MLB game-winner markets and map them to
    MLB Stats API gamePks (by team names + date)."""

    def __init__(self) -> None:
        self._http = None

    @property
    def http(self):
        if self._http is None:
            self._http = _client()
        return self._http

    def list_mlb_events(self, *, closed: bool = False, limit: int = 200) -> List[dict]:
        """Raw Gamma events tagged MLB. TODO(M1): confirm the current tag
        slug/series id for MLB single-game markets and pin it here."""
        resp = self.http.get(
            f"{GAMMA_BASE}/events",
            params={"tag_slug": "mlb", "closed": str(closed).lower(), "limit": limit},
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def parse_game_market(event: dict, game_pk: int) -> Optional[Market]:
        """Build a `Market` from a Gamma event known to be a game-winner
        market for `game_pk` (the caller does team/date matching; see
        bot.discover). Returns None when the event shape is unexpected."""
        try:
            market = event["markets"][0]
            token_ids = json.loads(market["clobTokenIds"])
            outcomes = json.loads(market["outcomes"])
        except (KeyError, IndexError, ValueError, TypeError):
            log.debug("unparseable gamma event: %s", event.get("slug", "?"))
            return None
        if len(token_ids) != 2 or len(outcomes) != 2:
            return None
        # Gamma orders tokens to match `outcomes`; map outcome names to
        # home/away via the caller's team-name matching. Placeholder maps
        # outcome[0] -> away (Polymarket convention "Team A vs Team B" varies:
        # TODO(M1) verify per-market and match on names, not order).
        return Market(
            condition_id=market.get("conditionId", ""),
            game_pk=game_pk,
            home_token_id=token_ids[1],
            away_token_id=token_ids[0],
            home_team=outcomes[1],
            away_team=outcomes[0],
        )


class ClobMarketData:
    """Order book snapshots from the CLOB REST API. Polling is fine for paper
    trading at a 2s tick; switch to the market websocket at M1 for live."""

    def __init__(self) -> None:
        self._http = None

    @property
    def http(self):
        if self._http is None:
            self._http = _client()
        return self._http

    def get_book_top(self, token_id: str) -> BookTop:
        resp = self.http.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        bids = data.get("bids") or []
        asks = data.get("asks") or []
        # CLOB returns price levels as strings, best level LAST in each list.
        best_bid = max(bids, key=lambda l: float(l["price"]), default=None)
        best_ask = min(asks, key=lambda l: float(l["price"]), default=None)
        return BookTop(
            token_id=token_id,
            bid=float(best_bid["price"]) if best_bid else None,
            bid_size=float(best_bid["size"]) if best_bid else 0.0,
            ask=float(best_ask["price"]) if best_ask else None,
            ask_size=float(best_ask["size"]) if best_ask else 0.0,
            fetched_at=time.time(),
        )
