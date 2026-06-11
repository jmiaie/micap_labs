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


@dataclass(frozen=True)
class RestingOrder:
    """A passive buy resting in the (simulated) book. At most one per token."""

    token_id: str
    side: Side
    limit_price: float
    size_shares: float
    placed_at: float

    @property
    def cost(self) -> float:
        return self.limit_price * self.size_shares


class Broker(Protocol):
    def buy(self, token_id: str, side: Side, limit_price: float, size_shares: float,
            book: BookTop, cross: bool) -> Optional[Fill]: ...

    def sell(self, token_id: str, side: Side, limit_price: float, size_shares: float,
             book: BookTop, cross: bool) -> Optional[Fill]: ...

    def resting_order(self, token_id: str) -> Optional[RestingOrder]: ...

    def try_fill_resting(self, token_id: str, book: BookTop, now: float) -> Optional[Fill]: ...

    def resting_count(self) -> int: ...

    def resting_cost(self) -> float: ...

    def cancel_all(self, token_id: str) -> None: ...


@dataclass
class PaperBroker:
    """Conservative fill simulation against book snapshots:

      - crossing buys fill immediately at the touch, capped at displayed size;
      - passive buys REST (one per token, cash reserved up front) and fill
        only on strict trade-through — the ask must move BELOW the limit
        price — which assumes worst queue position at our own level. Fills
        are at the limit price, full size;
      - sells always cross (exit urgency; passive exits are a roadmap item).

    Cash accounting only; share inventory lives in PortfolioRisk positions.
    The caller drives resting-order lifecycle each tick: `try_fill_resting`
    to match, `cancel_all` for cancel-on-event (refunds the reservation).
    """

    cash: float
    fills: list[Fill] = field(default_factory=list)
    _resting: Dict[str, RestingOrder] = field(default_factory=dict)

    def buy(self, token_id: str, side: Side, limit_price: float, size_shares: float,
            book: BookTop, cross: bool) -> Optional[Fill]:
        if size_shares <= 0:
            return None
        marketable = book.ask is not None and limit_price >= book.ask
        if cross or marketable:
            if book.ask is None:
                return None
            size = min(size_shares, book.ask_size)
            cost = book.ask * size
            if size <= 0 or cost > self.cash:
                return None
            self.cash -= cost
            fill = Fill(token_id, side, True, book.ask, size, time.time())
            self.fills.append(fill)
            return fill
        # Passive: rest the order, reserving cash. Replace any prior order on
        # this token (refund first so the affordability check is fair).
        self.cancel_all(token_id)
        order = RestingOrder(token_id, side, limit_price, size_shares, time.time())
        if order.cost > self.cash:
            return None
        self.cash -= order.cost
        self._resting[token_id] = order
        return None

    def resting_order(self, token_id: str) -> Optional[RestingOrder]:
        return self._resting.get(token_id)

    def resting_count(self) -> int:
        return len(self._resting)

    def resting_cost(self) -> float:
        return sum(o.cost for o in self._resting.values())

    def try_fill_resting(self, token_id: str, book: BookTop, now: float) -> Optional[Fill]:
        order = self._resting.get(token_id)
        if order is None or book.ask is None or book.ask >= order.limit_price:
            return None  # no strict trade-through: assume we never got filled
        del self._resting[token_id]
        # Cash was reserved at placement and the fill is at the limit price,
        # so no further cash movement here.
        fill = Fill(token_id, order.side, True, order.limit_price, order.size_shares, now)
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
        order = self._resting.pop(token_id, None)
        if order is not None:
            self.cash += order.cost  # release the reservation


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

    def resting_order(self, token_id: str) -> Optional[RestingOrder]:
        # M4: report venue open orders here (reconciled at startup). Returning
        # None keeps the caller's resting-management loop a safe no-op.
        return None

    def try_fill_resting(self, token_id: str, book: BookTop, now: float) -> Optional[Fill]:
        # M4: live fills arrive from venue order/trade events, not from local
        # matching against a snapshot.
        return None

    def resting_count(self) -> int:
        return 0  # M4: venue open-order count

    def resting_cost(self) -> float:
        return 0.0  # M4: venue open-order notional

    def cancel_all(self, token_id: str) -> None:
        raise NotImplementedError("live trading is gated until M4; run paper mode")
