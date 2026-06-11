"""Core data contracts shared across feeds, strategy, risk, and execution.

Everything here is stdlib-only and immutable-ish by convention: feeds produce
these snapshots, strategy functions consume them, and the broker/journal record
them. See ARCHITECTURE.md §5.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Half(str, Enum):
    TOP = "top"
    BOTTOM = "bottom"


class Side(str, Enum):
    """Which team's YES share a quote/position refers to."""

    HOME = "home"
    AWAY = "away"

    @property
    def opponent(self) -> "Side":
        return Side.AWAY if self is Side.HOME else Side.HOME


class ExitReason(str, Enum):
    TAKE_PROFIT = "take_profit"        # unrealized return >= take_profit_return
    SALVAGE = "salvage"                # model edge flipped negative beyond threshold
    EDGE_CONVERGED = "edge_converged"  # bid reached fair - exit_edge
    TIME_STOP = "time_stop"            # inning past time_stop_inning
    KILL_SWITCH = "kill_switch"        # risk halt: flatten everything
    RESOLVED = "resolved"              # game ended; venue settles 0/1


@dataclass(frozen=True)
class GameState:
    """Snapshot of an MLB game from the live feed (or simulator)."""

    game_pk: int
    home_team: str
    away_team: str
    inning: int                  # 1-based; >= 10 means extras
    half: Half
    outs: int                    # 0-2 (3 only transiently; feeds normalize)
    on_first: bool
    on_second: bool
    on_third: bool
    home_score: int
    away_score: int
    is_final: bool
    fetched_at: float            # unix seconds
    last_score_change_at: Optional[float] = None

    @property
    def score_diff_home(self) -> int:
        return self.home_score - self.away_score

    def age_s(self, now: Optional[float] = None) -> float:
        return (now if now is not None else time.time()) - self.fetched_at

    def seconds_since_score_change(self, now: Optional[float] = None) -> float:
        if self.last_score_change_at is None:
            return float("inf")
        return (now if now is not None else time.time()) - self.last_score_change_at


@dataclass(frozen=True)
class BookTop:
    """Top of book for one outcome token (prices in probability units, 0-1)."""

    token_id: str
    bid: Optional[float]
    bid_size: float              # shares at the bid
    ask: Optional[float]
    ask_size: float
    fetched_at: float

    @property
    def spread(self) -> float:
        if self.bid is None or self.ask is None:
            return float("inf")
        return self.ask - self.bid

    @property
    def mid(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2.0

    def age_s(self, now: Optional[float] = None) -> float:
        return (now if now is not None else time.time()) - self.fetched_at


@dataclass(frozen=True)
class Market:
    """A Polymarket game-winner market mapped to an MLB game."""

    condition_id: str
    game_pk: int
    home_token_id: str
    away_token_id: str
    home_team: str
    away_team: str

    def token_for(self, side: Side) -> str:
        return self.home_token_id if side is Side.HOME else self.away_token_id


@dataclass(frozen=True)
class FairValue:
    """Blended fair probability for one side of one market."""

    side: Side
    prob: float                  # blended fair win prob for `side`
    model_prob: Optional[float]
    sharp_prob: Optional[float]
    fresh: bool                  # False -> stand down, do not trade
    computed_at: float


@dataclass(frozen=True)
class EntrySignal:
    market: Market
    side: Side
    limit_price: float           # passive price we are willing to pay
    cross: bool                  # True -> take the ask instead of resting
    edge: float                  # fair - effective entry price
    fair: float
    size_shares: float           # risk-approved size


@dataclass(frozen=True)
class ExitSignal:
    reason: ExitReason
    limit_price: float           # price to sell at (bid for urgency, fair-ish for passive)
    cross: bool


@dataclass
class Position:
    market: Market
    side: Side
    entry_price: float
    size_shares: float
    entry_edge: float            # predicted edge at entry; for realization analysis
    opened_at: float
    closed_at: Optional[float] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[ExitReason] = None

    @property
    def cost(self) -> float:
        return self.entry_price * self.size_shares

    def unrealized_return(self, bid: float) -> float:
        """Fractional return if sold at `bid` (e.g. 0.5 == +50%)."""
        if self.entry_price <= 0:
            return 0.0
        return (bid - self.entry_price) / self.entry_price

    @property
    def realized_pnl(self) -> Optional[float]:
        if self.exit_price is None:
            return None
        return (self.exit_price - self.entry_price) * self.size_shares


@dataclass
class TradeRecord:
    """One closed round trip, with everything needed for offline analysis:
    calibration (model vs market vs outcome) and edge realization
    (realized pnl / predicted edge). See STRATEGY_ASSESSMENT.md §7.
    """

    game_pk: int
    side: Side
    entry_price: float
    exit_price: float
    size_shares: float
    entry_edge: float
    entry_fair: float
    exit_reason: ExitReason
    opened_at: float
    closed_at: float
    pnl: float = field(default=0.0)

    def __post_init__(self) -> None:
        self.pnl = (self.exit_price - self.entry_price) * self.size_shares
