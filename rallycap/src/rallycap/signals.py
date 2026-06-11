"""Entry and exit decision logic (PRD §5.3–5.4).

Pure functions over (game state, book, fair value, position, config): no I/O,
no clocks (timestamps come in on the snapshots), fully unit-testable. The bot
loop and the backtester both call exactly these functions.
"""

from __future__ import annotations

from typing import Optional

from .config import BotConfig
from .types import (
    BookTop,
    EntrySignal,
    ExitReason,
    ExitSignal,
    FairValue,
    GameState,
    Market,
    Position,
    Side,
)


def in_underdog_band(price: float, cfg: BotConfig) -> bool:
    lo, hi = cfg.underdog_price_band
    return lo <= price <= hi


def screen_side(state: GameState, book: BookTop, side: Side, cfg: BotConfig) -> bool:
    """Universe screen (PRD 5.1) for taking `side` at its current ask."""
    if state.inning > cfg.max_entry_inning:
        return False
    if book.ask is None or book.bid is None:
        return False
    if book.spread > cfg.max_spread:
        return False
    if book.ask_size * book.ask < cfg.min_book_depth_usd:
        return False
    if in_underdog_band(book.ask, cfg):
        return True
    # Optionally take the favorite when IT is the mispriced side: its
    # complement must put the game in the lopsided screen.
    return cfg.allow_favorite_side and in_underdog_band(1.0 - book.ask, cfg)


def evaluate_entry(
    market: Market,
    state: GameState,
    book: BookTop,
    fair: FairValue,
    side: Side,
    size_shares: float,
    cfg: BotConfig,
    now: float,
) -> Optional[EntrySignal]:
    """All gates from PRD §5.3. `size_shares` is the risk-approved size
    (signals don't size; risk.py does). Returns None unless every gate holds.
    """
    if size_shares <= 0:
        return None
    if not fair.fresh:
        return None
    if book.age_s(now) > cfg.max_feed_age_s:
        return None
    if not screen_side(state, book, side, cfg):
        return None
    assert book.ask is not None  # screen_side guarantees a two-sided book

    edge_at_ask = fair.prob - book.ask
    passive_price = round(min(book.ask - 0.01, fair.prob - cfg.keep_edge), 3)
    cross = edge_at_ask >= cfg.entry_edge + cfg.cross_margin

    if cross:
        price, edge = book.ask, edge_at_ask
    else:
        price, edge = passive_price, fair.prob - passive_price
        if passive_price <= (book.bid or 0.0):
            return None  # passive price not actually inside the book
    if fair.prob - price < cfg.entry_edge:
        return None

    return EntrySignal(
        market=market,
        side=side,
        limit_price=price,
        cross=cross,
        edge=edge,
        fair=fair.prob,
        size_shares=size_shares,
    )


def evaluate_exit(
    position: Position,
    state: GameState,
    book: BookTop,
    fair: FairValue,
    cfg: BotConfig,
) -> Optional[ExitSignal]:
    """Exit rules in PRD §5.4 priority order. First match wins.

    Note deliberate asymmetry vs entries: exits are allowed during freeze
    windows and on stale fair values for TAKE_PROFIT / TIME_STOP (which don't
    depend on the model), because being unable to exit is worse than exiting
    imperfectly. SALVAGE / EDGE_CONVERGED require a fresh fair value.
    """
    if state.is_final or book.bid is None:
        return None  # resolution settles the position; nothing to do here

    bid = book.bid

    # 1. Take-profit cap (the original strategy's +50% rule, default ON).
    if cfg.take_profit_enabled and position.unrealized_return(bid) >= cfg.take_profit_return:
        return ExitSignal(reason=ExitReason.TAKE_PROFIT, limit_price=bid, cross=True)

    if fair.fresh:
        edge_now = fair.prob - bid
        # 2. Salvage: thesis broke; the market is right and we are wrong.
        if edge_now <= -cfg.salvage_edge:
            return ExitSignal(reason=ExitReason.SALVAGE, limit_price=bid, cross=True)
        # 3. Edge convergence: thesis realized; take the win.
        if bid >= fair.prob - cfg.exit_edge:
            return ExitSignal(reason=ExitReason.EDGE_CONVERGED, limit_price=bid, cross=True)

    # 4. Time stop: past the early-game regime this thesis lives in.
    if state.inning > cfg.time_stop_inning:
        return ExitSignal(reason=ExitReason.TIME_STOP, limit_price=bid, cross=True)

    return None
