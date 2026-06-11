"""Portfolio risk: Kelly-based sizing clamped by hard caps, plus the daily
kill switch (PRD §5.5). The signal engine asks this module "how big, if at
all?" — a zero answer vetoes the trade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from .config import BotConfig
from .kelly import recommended_fraction
from .types import BookTop, FairValue, Position

log = logging.getLogger(__name__)


@dataclass
class PortfolioRisk:
    cfg: BotConfig
    bankroll: float
    open_positions: Dict[str, Position] = field(default_factory=dict)  # token_id -> pos
    daily_realized_pnl: float = 0.0
    killed: bool = False

    # ------------------------------------------------------------------ state
    @property
    def total_at_risk(self) -> float:
        return sum(p.cost for p in self.open_positions.values())

    def record_close(self, pnl: float) -> None:
        self.daily_realized_pnl += pnl
        self.bankroll += pnl
        if self.daily_realized_pnl <= -self.cfg.daily_loss_limit_pct * self.bankroll:
            if not self.killed:
                log.warning(
                    "KILL SWITCH: daily pnl %.2f breached %.1f%% of bankroll %.2f — halting",
                    self.daily_realized_pnl,
                    100 * self.cfg.daily_loss_limit_pct,
                    self.bankroll,
                )
            self.killed = True

    def reset_daily(self) -> None:
        self.daily_realized_pnl = 0.0
        self.killed = False

    # ----------------------------------------------------------------- sizing
    def approve_size(self, book: BookTop, fair: FairValue, entry_price: float,
                     pending_count: int = 0, pending_cost: float = 0.0) -> float:
        """Risk-approved size in SHARES for a prospective entry, or 0.0.

        Applies, in order: kill switch, concurrency cap, fractional Kelly,
        per-trade bankroll cap, total at-risk cap, and depth participation.

        `pending_count`/`pending_cost` are the broker's resting orders —
        commitments that may become positions without another risk check, so
        they count against the concurrency and at-risk caps now.
        """
        if self.killed:
            return 0.0
        if len(self.open_positions) + pending_count >= self.cfg.max_concurrent_positions:
            return 0.0
        if entry_price <= 0.0 or book.ask is None:
            return 0.0

        converge_exit = max(entry_price, fair.prob - self.cfg.exit_edge)
        f = recommended_fraction(
            entry_price=entry_price,
            fair=fair.prob,
            take_profit_return=self.cfg.take_profit_return,
            converge_exit_price=converge_exit,
            kelly_fraction=self.cfg.kelly_fraction,
        )
        if f <= 0.0:
            return 0.0

        stake = min(f, self.cfg.max_trade_pct) * self.bankroll
        headroom = (self.cfg.max_total_at_risk_pct * self.bankroll
                    - self.total_at_risk - pending_cost)
        stake = min(stake, max(0.0, headroom))
        # Don't be the whole top of book: fills beyond it are fictional anyway.
        depth_cap_shares = book.ask_size * self.cfg.max_depth_participation
        shares = min(stake / entry_price, depth_cap_shares)
        return max(0.0, round(shares, 2))

    # ------------------------------------------------------------- bookkeeping
    def register_open(self, token_id: str, position: Position) -> None:
        self.open_positions[token_id] = position

    def pop_position(self, token_id: str) -> Optional[Position]:
        return self.open_positions.pop(token_id, None)
