"""Orchestrator: the live/paper polling loop (ARCHITECTURE.md §4).

One synchronous tick every `cfg.poll_seconds` across all tracked games:
refresh feeds -> fair values -> exits -> entries -> journal. PaperBroker by
default; the live broker is gated until M4 (see execution/broker.py).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .config import BotConfig
from .execution.broker import Broker, PaperBroker, PolymarketBroker
from .fair_value import compute_fair_value
from .feeds import teams
from .feeds.mlb_statsapi import MlbStatsFeed
from .feeds.polymarket import ClobMarketData, GammaClient
from .feeds.sharp_odds import NullSharpOdds, SharpOddsSource
from .risk import PortfolioRisk
from .signals import evaluate_entry, evaluate_exit
from .types import ExitReason, GameState, Market, Position, Side, TradeRecord

log = logging.getLogger(__name__)


@dataclass
class TrackedGame:
    market: Market
    state: Optional[GameState] = None
    done: bool = False


@dataclass
class Bot:
    cfg: BotConfig
    games_feed: MlbStatsFeed = field(default_factory=MlbStatsFeed)
    market_data: ClobMarketData = field(default_factory=ClobMarketData)
    gamma: GammaClient = field(default_factory=GammaClient)
    sharp: SharpOddsSource = field(default_factory=NullSharpOdds)
    broker: Optional[Broker] = None
    risk: Optional[PortfolioRisk] = None
    tracked: Dict[int, TrackedGame] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.risk is None:
            self.risk = PortfolioRisk(cfg=self.cfg, bankroll=self.cfg.bankroll_usd)
        if self.broker is None:
            if self.cfg.mode == "live":
                self.broker = PolymarketBroker(
                    private_key=self.cfg.polymarket_private_key or "",
                    proxy_address=self.cfg.polymarket_proxy_address,
                )
            else:
                self.broker = PaperBroker(cash=self.cfg.bankroll_usd)

    # ------------------------------------------------------------- discovery
    def discover(self, date: Optional[str] = None) -> List[TrackedGame]:
        """Match the day's MLB schedule to Polymarket game-winner markets by
        canonical team identity (feeds/teams.py): a Gamma event is accepted
        for a game only when its two outcome labels resolve to exactly that
        game's home/away teams. Doubleheaders (same team pair twice in one
        day) are skipped — outcome labels can't distinguish game 1 from
        game 2, and a wrong join would trade the wrong game.
        """
        date = date or dt.date.today().isoformat()
        schedule = self.games_feed.schedule(date)
        events = self.gamma.list_mlb_events(closed=False)

        pair_counts: Dict[frozenset, int] = {}
        for game in schedule:
            pair = teams.team_pair(game["home_name"], game["away_name"])
            if pair is not None:
                pair_counts[pair] = pair_counts.get(pair, 0) + 1

        for game in schedule:
            pair = teams.team_pair(game["home_name"], game["away_name"])
            if pair is None:
                log.warning("gamePk %s: unrecognized teams %s / %s — skipping",
                            game["game_pk"], game["away_name"], game["home_name"])
                continue
            if pair_counts[pair] > 1:
                log.warning("gamePk %s: doubleheader (%s) — skipping, cannot "
                            "disambiguate markets", game["game_pk"], sorted(pair))
                continue
            for event in events:
                market = self.gamma.parse_game_market(
                    event, game["game_pk"], game["home_name"], game["away_name"]
                )
                if market is not None:
                    self.tracked[game["game_pk"]] = TrackedGame(market=market)
                    break
        log.info("discovery: tracking %d markets for %s", len(self.tracked), date)
        return list(self.tracked.values())

    # ------------------------------------------------------------------ loop
    def run(self) -> None:
        if self.cfg.mode == "live":
            log.warning("LIVE mode: orders are real. Caps: %.1f%%/trade, %d concurrent.",
                        100 * self.cfg.max_trade_pct, self.cfg.max_concurrent_positions)
        if not self.tracked:
            self.discover()
        self._load_state()
        try:
            while not all(g.done for g in self.tracked.values()):
                started = time.time()
                self.tick()
                self._save_state()
                time.sleep(max(0.0, self.cfg.poll_seconds - (time.time() - started)))
        finally:
            self._save_state()
            log.info("run complete: bankroll %.2f, daily pnl %.2f",
                     self.risk.bankroll, self.risk.daily_realized_pnl)

    def tick(self, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        for tg in self.tracked.values():
            if tg.done:
                continue
            try:
                self._tick_game(tg, now)
            except Exception:
                log.exception("gamePk %s: tick failed; standing down this game", tg.market.game_pk)

    def _tick_game(self, tg: TrackedGame, now: float) -> None:
        state = self.games_feed.live_state(tg.market.game_pk)
        if state is None:
            return
        tg.state = state

        # Exits first: never let a new entry compete with risk reduction.
        for side in (Side.HOME, Side.AWAY):
            token = tg.market.token_for(side)
            pos = self.risk.open_positions.get(token)
            if pos is None:
                continue
            book = self.market_data.get_book_top(token)
            sharp_home = self.sharp.live_home_prob(tg.market.game_pk)
            sharp = self._side_prob(sharp_home, side)
            fair = compute_fair_value(state, side, self.cfg, sharp_prob=sharp, now=now)
            if state.is_final:
                self._settle(token, pos, state, now)
                continue
            sig = evaluate_exit(pos, state, book, fair, self.cfg)
            if sig is None:
                continue
            fill = self.broker.sell(token, pos.side, sig.limit_price, pos.size_shares, book, sig.cross)
            if fill is not None:
                self._close(token, pos, fill.price, fill.size_shares, sig.reason, now)

        if state.is_final:
            tg.done = True
            return
        if self.risk.killed:
            return

        # Entries.
        for side in (Side.HOME, Side.AWAY):
            token = tg.market.token_for(side)
            if token in self.risk.open_positions or tg.market.token_for(side.opponent) in self.risk.open_positions:
                continue
            book = self.market_data.get_book_top(token)
            sharp = self._side_prob(self.sharp.live_home_prob(tg.market.game_pk), side)
            fair = compute_fair_value(state, side, self.cfg, sharp_prob=sharp, now=now)
            if book.ask is None:
                continue
            size = self.risk.approve_size(book, fair, book.ask)
            sig = evaluate_entry(tg.market, state, book, fair, side, size, self.cfg, now)
            if sig is None:
                continue
            fill = self.broker.buy(token, side, sig.limit_price, sig.size_shares, book, sig.cross)
            if fill is not None:
                pos = Position(
                    market=tg.market, side=side, entry_price=fill.price,
                    size_shares=fill.size_shares, entry_edge=sig.fair - fill.price,
                    opened_at=now,
                )
                self.risk.register_open(token, pos)
                log.info("OPEN %s %s %.2f x %.1f (fair %.3f, edge %.3f)",
                         tg.market.game_pk, side.value, fill.price, fill.size_shares,
                         sig.fair, pos.entry_edge)
                break  # one position per game

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _side_prob(home_prob: Optional[float], side: Side) -> Optional[float]:
        if home_prob is None:
            return None
        return home_prob if side is Side.HOME else 1.0 - home_prob

    def _settle(self, token: str, pos: Position, final: GameState, now: float) -> None:
        home_won = final.home_score > final.away_score
        won = home_won if pos.side is Side.HOME else not home_won
        if isinstance(self.broker, PaperBroker):
            self.broker.settle_resolution(token, pos.side, pos.size_shares, won)
        self._close(token, pos, 1.0 if won else 0.0, pos.size_shares, ExitReason.RESOLVED, now)

    def _close(self, token: str, pos: Position, price: float, size: float,
               reason: ExitReason, now: float) -> None:
        record = TradeRecord(
            game_pk=pos.market.game_pk, side=pos.side, entry_price=pos.entry_price,
            exit_price=price, size_shares=size, entry_edge=pos.entry_edge,
            entry_fair=pos.entry_price + pos.entry_edge, exit_reason=reason,
            opened_at=pos.opened_at, closed_at=now,
        )
        self._append_trade_log(record)
        log.info("CLOSE %s %s %s %.2f -> %.2f pnl %+.2f",
                 pos.market.game_pk, pos.side.value, reason.value,
                 pos.entry_price, price, record.pnl)
        if size >= pos.size_shares - 1e-9:
            self.risk.pop_position(token)
        else:
            pos.size_shares -= size
        self.risk.record_close(record.pnl)

    # ---------------------------------------------------------- persistence
    def _append_trade_log(self, record: TradeRecord) -> None:
        row = {**record.__dict__, "side": record.side.value, "exit_reason": record.exit_reason.value}
        with open(self.cfg.trade_log_path, "a") as f:
            f.write(json.dumps(row) + "\n")

    def _save_state(self) -> None:
        state = {
            "saved_at": time.time(),
            "mode": self.cfg.mode,
            "bankroll": self.risk.bankroll,
            "daily_realized_pnl": self.risk.daily_realized_pnl,
            "killed": self.risk.killed,
            "open_positions": {
                token: {
                    "game_pk": p.market.game_pk, "side": p.side.value,
                    "entry_price": p.entry_price, "size_shares": p.size_shares,
                    "entry_edge": p.entry_edge, "opened_at": p.opened_at,
                }
                for token, p in self.risk.open_positions.items()
            },
        }
        Path(self.cfg.state_path).write_text(json.dumps(state, indent=2))

    def _load_state(self) -> None:
        """v0 restores bankroll/kill state only. TODO(M1): full position and
        open-order reconciliation against the venue (PRD F7/F9)."""
        path = Path(self.cfg.state_path)
        if not path.exists():
            return
        data = json.loads(path.read_text())
        if data.get("mode") == self.cfg.mode:
            self.risk.bankroll = data.get("bankroll", self.risk.bankroll)
            self.risk.daily_realized_pnl = data.get("daily_realized_pnl", 0.0)
            self.risk.killed = data.get("killed", False)
            if data.get("open_positions"):
                log.warning("state file has open positions; v0 cannot reconcile them — "
                            "verify on the venue before continuing")
