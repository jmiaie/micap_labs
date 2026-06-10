"""Broker abstraction: PaperBroker (deterministic simulation, the default)
and PolymarketBroker (live, via py-clob-client — scaffolded, not yet armed).

Both expose the same minimal surface so the bot loop and backtester run the
identical strategy path (ARCHITECTURE.md §2: a backtest is a rehearsal of
production code, not a parallel implementation).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol

from ..types import BookTop, Side

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fill:
    token_id: str
    side: Side
    is_buy: bool
    price: float
    size_shares: float
    filled_at: float


class Broker(Protocol):
    def buy(self, token_id: str, side: Side, limit_price: float, size_shares: float,
            book: BookTop, cross: bool) -> Optional[Fill]: ...

    def sell(self, token_id: str, side: Side, limit_price: float, size_shares: float,
             book: BookTop, cross: bool) -> Optional[Fill]: ...

    def cancel_all(self, token_id: str) -> None: ...


@dataclass
class PaperBroker:
    """Conservative immediate-or-cancel fill simulation against the snapshot:

      - crossing orders fill at the touch, capped at displayed size;
      - passive orders do NOT fill (worst-case queue assumption — real resting
        fills are a bonus the backtest refuses to count; see PRD F8).

    Cash accounting only; share inventory lives in PortfolioRisk positions.
    """

    cash: float
    fills: list[Fill] = field(default_factory=list)

    def buy(self, token_id: str, side: Side, limit_price: float, size_shares: float,
            book: BookTop, cross: bool) -> Optional[Fill]:
        if not cross or book.ask is None or limit_price < book.ask:
            return None  # resting order: assume no fill (conservative)
        size = min(size_shares, book.ask_size)
        cost = book.ask * size
        if size <= 0 or cost > self.cash:
            return None
        self.cash -= cost
        fill = Fill(token_id, side, True, book.ask, size, time.time())
        self.fills.append(fill)
        return fill

    def sell(self, token_id: str, side: Side, limit_price: float, size_shares: float,
             book: BookTop, cross: bool) -> Optional[Fill]:
        if book.bid is None or (not cross and book.bid < limit_price):
            return None
        size = min(size_shares, book.bid_size) if cross else size_shares
        if size <= 0:
            return None
        self.cash += book.bid * size
        fill = Fill(token_id, side, False, book.bid, size, time.time())
        self.fills.append(fill)
        return fill

    def settle_resolution(self, token_id: str, side: Side, size_shares: float, won: bool) -> Fill:
        """Game ended while holding: venue settles shares at 1 or 0."""
        payout = 1.0 if won else 0.0
        self.cash += payout * size_shares
        fill = Fill(token_id, side, False, payout, size_shares, time.time())
        self.fills.append(fill)
        return fill

    def cancel_all(self, token_id: str) -> None:
        return None  # paper resting orders never exist (see buy())


class PolymarketBroker:
    """Live order placement through the official CLOB client.

    Deliberately unfinished: every order path raises until M4 arming. The
    integration points are documented so wiring is mechanical:

      from py_clob_client.client import ClobClient
      client = ClobClient(host="https://clob.polymarket.com", key=<PRIVATE_KEY>,
                          chain_id=137, signature_type=..., funder=<PROXY>)
      client.set_api_creds(client.create_or_derive_api_creds())
      client.create_and_post_order(OrderArgs(price=..., size=..., side=BUY,
                                             token_id=...))

    Requirements before arming (PRD §7, M4 gate):
      - idempotent client order ids + restart reconciliation,
      - cancel-on-event wired to the freeze detector,
      - micro-stakes caps enforced in config, paper parity report attached.
    """

    def __init__(self, private_key: str, proxy_address: Optional[str] = None) -> None:
        if not private_key:
            raise ValueError("live broker requires POLYMARKET_PRIVATE_KEY")
        self._private_key = private_key
        self._proxy = proxy_address
        self._client = None  # lazy py_clob_client.ClobClient

    def buy(self, *args, **kwargs) -> Optional[Fill]:
        raise NotImplementedError("live trading is gated until M4; run paper mode")

    def sell(self, *args, **kwargs) -> Optional[Fill]:
        raise NotImplementedError("live trading is gated until M4; run paper mode")

    def cancel_all(self, token_id: str) -> None:
        raise NotImplementedError("live trading is gated until M4; run paper mode")
