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

from . import teams
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
    def parse_game_market(
        event: dict, game_pk: int, home_label: str, away_label: str
    ) -> Optional[Market]:
        """Build a `Market` from a Gamma event IF its two outcomes match the
        expected home/away teams (by canonical team identity, not array
        order — Polymarket outcome ordering is not a home/away contract).

        Returns None when the event shape is unexpected or the outcomes
        don't unambiguously map onto this game's teams, so the caller can
        simply try the next event.
        """
        try:
            market = event["markets"][0]
            token_ids = json.loads(market["clobTokenIds"])
            outcomes = json.loads(market["outcomes"])
        except (KeyError, IndexError, ValueError, TypeError):
            log.debug("unparseable gamma event: %s", event.get("slug", "?"))
            return None
        if len(token_ids) != 2 or len(outcomes) != 2:
            return None

        home_abbr, away_abbr = teams.match_team(home_label), teams.match_team(away_label)
        matched = [teams.match_team(o) for o in outcomes]
        if home_abbr is None or away_abbr is None or None in matched:
            return None
        if set(matched) != {home_abbr, away_abbr}:
            return None
        home_idx = matched.index(home_abbr)
        return Market(
            condition_id=market.get("conditionId", ""),
            game_pk=game_pk,
            home_token_id=token_ids[home_idx],
            away_token_id=token_ids[1 - home_idx],
            home_team=outcomes[home_idx],
            away_team=outcomes[1 - home_idx],
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
        return self.parse_book(token_id, resp.json(), now=time.time())

    @staticmethod
    def parse_book(token_id: str, data: dict[str, Any], now: float) -> BookTop:
        """Pure parser for a CLOB /book payload. Prices/sizes arrive as
        strings; level ordering is not relied upon (best bid = max price,
        best ask = min price)."""
        bids = data.get("bids") or []
        asks = data.get("asks") or []
        best_bid = max(bids, key=lambda l: float(l["price"]), default=None)
        best_ask = min(asks, key=lambda l: float(l["price"]), default=None)
        return BookTop(
            token_id=token_id,
            bid=float(best_bid["price"]) if best_bid else None,
            bid_size=float(best_bid["size"]) if best_bid else 0.0,
            ask=float(best_ask["price"]) if best_ask else None,
            ask_size=float(best_ask["size"]) if best_ask else 0.0,
            fetched_at=now,
        )
