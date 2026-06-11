"""Event-driven backtester + synthetic game/market simulator.

The engine drives the SAME pipeline the live bot runs (fair value -> signals
-> risk -> broker), against either synthetic games (offline, day one) or
historical data (loaders are an M2 deliverable; see `HistoricalDataset`).

THE SIMULATOR IS FOR PLUMBING, NOT VALIDATION. It generates games from the
same family of dynamics the WP model assumes and injects overreaction shocks
by construction — so the strategy is profitable here *by design*. Its job is
to exercise every code path and demonstrate the mechanics. Do not tune
strategy parameters against it (PRD risk table: "overfitting the simulator").
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from ..config import BotConfig
from ..execution.broker import PaperBroker
from ..fair_value import TeamRatings, compute_fair_value
from ..ratings import ratings_from_pregame_prob
from ..risk import PortfolioRisk
from ..signals import evaluate_entry, evaluate_exit
from ..types import (
    BookTop,
    ExitReason,
    GameState,
    Half,
    Market,
    Position,
    Side,
    TradeRecord,
)
from ..wp_model import win_probability

# ---------------------------------------------------------------------------
# Synthetic simulator
# ---------------------------------------------------------------------------

# Per-half-inning run distribution, mean ~0.51 (league average).
RUNS_PMF: List[Tuple[int, float]] = [(0, 0.72), (1, 0.15), (2, 0.07), (3, 0.035), (4, 0.015), (5, 0.01)]


@dataclass
class SimParams:
    overreaction: float = 0.12     # market overshoot after a scoring play, prob units
    decay_tau_s: float = 240.0     # overshoot decay (exponential tau); slow enough
                                   # that edge survives the post-event freeze window
    noise_sigma: float = 0.008     # AR(1) market noise per tick
    spread: float = 0.02
    depth_shares: Tuple[float, float] = (150.0, 600.0)
    seconds_per_tick: float = 40.0


@dataclass
class Tick:
    state: GameState
    book_home: BookTop
    book_away: BookTop


class SyntheticGame:
    """One simulated game: true win probability follows the WP model over a
    simulated play-by-play; the market price is truth + decaying overreaction
    shocks + AR noise, quoted with a fixed spread."""

    def __init__(self, game_pk: int, rng: random.Random, params: SimParams, start_time: float = 0.0):
        self.game_pk = game_pk
        self.rng = rng
        self.p = params
        self.now = start_time
        self.bias = 0.0
        self.noise = 0.0
        self.last_score_change_at: Optional[float] = None

    def _sample_runs(self) -> int:
        x, acc = self.rng.random(), 0.0
        for runs, prob in RUNS_PMF:
            acc += prob
            if x <= acc:
                return runs
        return 0

    def _market_books(self, state: GameState) -> Tuple[BookTop, BookTop]:
        true_wp = win_probability(state)
        self.noise = 0.9 * self.noise + self.rng.gauss(0.0, self.p.noise_sigma)
        p_home = min(0.98, max(0.02, true_wp + self.bias + self.noise))
        half_spread = self.p.spread / 2.0
        lo, hi = self.p.depth_shares

        def book(token: str, prob: float) -> BookTop:
            return BookTop(
                token_id=token,
                bid=round(max(0.01, prob - half_spread), 3),
                bid_size=self.rng.uniform(lo, hi),
                ask=round(min(0.99, prob + half_spread), 3),
                ask_size=self.rng.uniform(lo, hi),
                fetched_at=self.now,
            )

        return book(f"{self.game_pk}-H", p_home), book(f"{self.game_pk}-A", 1.0 - p_home)

    def _state(self, inning: int, half: Half, outs: int, home: int, away: int,
               final: bool = False) -> GameState:
        r = self.rng.random
        return GameState(
            game_pk=self.game_pk,
            home_team="HOME", away_team="AWAY",
            inning=inning, half=half, outs=outs,
            on_first=r() < 0.30, on_second=r() < 0.18, on_third=r() < 0.08,
            home_score=home, away_score=away,
            is_final=final,
            fetched_at=self.now,
            last_score_change_at=self.last_score_change_at,
        )

    def ticks(self) -> Iterator[Tick]:
        home = away = 0
        inning = 1
        while True:
            for half in (Half.TOP, Half.BOTTOM):
                if half is Half.BOTTOM and inning >= 9 and home > away:
                    break  # home leads going to the bottom late: game over
                runs = self._sample_runs()
                run_outs = sorted(self.rng.sample(range(3), k=min(runs, 3))) if runs else []
                scored_so_far = 0
                for out_step in range(3):
                    self.now += self.p.seconds_per_tick
                    self.bias *= math.exp(-self.p.seconds_per_tick / self.p.decay_tau_s)
                    if out_step in run_outs:
                        batch = runs - scored_so_far if out_step == run_outs[-1] else 1
                        scored_so_far += batch
                        wp_before = win_probability(self._state(inning, half, out_step, home, away))
                        if half is Half.TOP:
                            away += batch
                        else:
                            home += batch
                        wp_after = win_probability(self._state(inning, half, out_step, home, away))
                        # Overshoot in the scoring team's direction, scaled by
                        # how big the true repricing was.
                        jump = wp_after - wp_before
                        self.bias += self.p.overreaction * (1 if jump > 0 else -1) * min(
                            1.0, abs(jump) / 0.10
                        )
                        self.last_score_change_at = self.now
                    state = self._state(inning, half, out_step, home, away)
                    bh, ba = self._market_books(state)
                    yield Tick(state, bh, ba)
                if half is Half.BOTTOM and inning >= 9 and home != away:
                    break
            if inning >= 9 and home != away:
                break
            if inning >= 12 and home == away:  # sudden-death cap for the sim
                if self.rng.random() < 0.52:
                    home += 1
                else:
                    away += 1
                break
            inning += 1
        self.now += self.p.seconds_per_tick
        final = self._state(min(inning, 12), Half.BOTTOM, 2, home, away, final=True)
        bh, ba = self._market_books(final)
        yield Tick(final, bh, ba)


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    trades: List[TradeRecord] = field(default_factory=list)
    ending_bankroll: float = 0.0
    starting_bankroll: float = 0.0
    max_drawdown: float = 0.0
    games: int = 0

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def win_rate(self) -> Optional[float]:
        if not self.trades:
            return None
        return sum(1 for t in self.trades if t.pnl > 0) / len(self.trades)

    @property
    def avg_return_per_trade(self) -> Optional[float]:
        rets = [t.pnl / (t.entry_price * t.size_shares) for t in self.trades
                if t.entry_price * t.size_shares > 0]
        return sum(rets) / len(rets) if rets else None

    @property
    def edge_realization(self) -> Optional[float]:
        """Realized PnL / predicted edge dollars (assessment §7 gate ~>= 0.5)."""
        predicted = sum(t.entry_edge * t.size_shares for t in self.trades)
        return self.total_pnl / predicted if predicted > 0 else None

    def exit_reason_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for t in self.trades:
            counts[t.exit_reason.value] = counts.get(t.exit_reason.value, 0) + 1
        return counts

    def summary(self) -> str:
        wr = f"{self.win_rate:.1%}" if self.win_rate is not None else "n/a"
        ar = f"{self.avg_return_per_trade:+.1%}" if self.avg_return_per_trade is not None else "n/a"
        er = f"{self.edge_realization:.2f}" if self.edge_realization is not None else "n/a"
        roi = (self.ending_bankroll - self.starting_bankroll) / self.starting_bankroll
        return (
            f"games={self.games} trades={self.n_trades} win_rate={wr} "
            f"avg_ret/trade={ar} edge_realization={er}\n"
            f"bankroll: {self.starting_bankroll:.2f} -> {self.ending_bankroll:.2f} "
            f"({roi:+.2%}) max_drawdown={self.max_drawdown:.2%}\n"
            f"exits: {self.exit_reason_counts()}"
        )


class BacktestEngine:
    """Runs the production pipeline over a stream of ticks per game."""

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.risk = PortfolioRisk(cfg=cfg, bankroll=cfg.bankroll_usd)
        self.broker = PaperBroker(cash=cfg.bankroll_usd)
        self.result = BacktestResult(
            starting_bankroll=cfg.bankroll_usd, ending_bankroll=cfg.bankroll_usd
        )
        self._equity_peak = cfg.bankroll_usd

    # ------------------------------------------------------------------ public
    def run_synthetic(self, n_games: int, seed: int = 7, params: Optional[SimParams] = None) -> BacktestResult:
        rng = random.Random(seed)
        params = params or SimParams()
        t0 = 0.0
        for i in range(n_games):
            game = SyntheticGame(game_pk=100_000 + i, rng=rng, params=params, start_time=t0)
            market = Market(
                condition_id=f"sim-{i}",
                game_pk=game.game_pk,
                home_token_id=f"{game.game_pk}-H",
                away_token_id=f"{game.game_pk}-A",
                home_team="HOME", away_team="AWAY",
            )
            self.risk.reset_daily()  # one game ~= one trading day in the sim
            self._run_game(market, game.ticks())
            t0 = game.now + 3600.0
        self.result.ending_bankroll = self.risk.bankroll
        self.result.games = n_games
        return self.result

    # ----------------------------------------------------------------- internals
    def _book_for(self, tick: Tick, side: Side) -> BookTop:
        return tick.book_home if side is Side.HOME else tick.book_away

    def _run_game(self, market: Market, ticks: Iterator[Tick]) -> None:
        ratings: Optional[TeamRatings] = None
        for tick in ticks:
            now = tick.state.fetched_at
            if ratings is None:
                # Anchor priors to the first market quote, the same way the
                # live bot does at discovery — but only if the game is still
                # effectively pregame; an in-game price must never be read as
                # team quality (same guard as Bot._pregame_ratings).
                s, mid = tick.state, tick.book_home.mid
                pregame_ish = (s.inning == 1 and s.half is Half.TOP
                               and s.home_score == 0 and s.away_score == 0)
                ratings = (ratings_from_pregame_prob(mid)
                           if pregame_ish and mid is not None else TeamRatings())
            self._handle_exits(market, tick, now, ratings)
            if tick.state.is_final:
                self._cancel_market_orders(market)
                self._settle(market, tick.state)
            else:
                self._handle_entries(market, tick, now, ratings)
            self._mark_equity(tick)

    def _open_position_on(self, market: Market) -> Optional[Tuple[str, Position]]:
        for side in (Side.HOME, Side.AWAY):
            token = market.token_for(side)
            pos = self.risk.open_positions.get(token)
            if pos is not None:
                return token, pos
        return None

    def _handle_exits(self, market: Market, tick: Tick, now: float,
                      ratings: TeamRatings) -> None:
        found = self._open_position_on(market)
        if found is None:
            return
        token, pos = found
        book = self._book_for(tick, pos.side)
        fair = compute_fair_value(tick.state, pos.side, self.cfg, ratings=ratings, now=now)
        sig = evaluate_exit(pos, tick.state, book, fair, self.cfg)
        if sig is None:
            return
        fill = self.broker.sell(token, pos.side, sig.limit_price, pos.size_shares, book, sig.cross)
        if fill is None:
            return
        self._record_close(token, pos, fill.price, fill.size_shares, sig.reason, now)

    def _cancel_market_orders(self, market: Market) -> None:
        for side in (Side.HOME, Side.AWAY):
            self.broker.cancel_all(market.token_for(side))

    def _manage_resting(self, market: Market, tick: Tick, now: float,
                        ratings: TeamRatings) -> bool:
        """Resting-order lifecycle (cancel-on-event, then matching). Returns
        True if an order is still resting or just filled — either way no new
        entry may be placed on this market this tick."""
        engaged = False
        for side in (Side.HOME, Side.AWAY):
            token = market.token_for(side)
            order = self.broker.resting_order(token)
            if order is None:
                continue
            book = self._book_for(tick, side)
            fair = compute_fair_value(tick.state, side, self.cfg, ratings=ratings, now=now)
            # Cancel-on-event: freeze/staleness, thesis gone, window closed,
            # or the kill switch — a resting quote must never outlive the
            # conditions that justified it.
            stale_edge = fair.prob - order.limit_price < self.cfg.entry_edge
            if (self.risk.killed or not fair.fresh or stale_edge
                    or tick.state.inning > self.cfg.max_entry_inning):
                self.broker.cancel_all(token)
                continue
            fill = self.broker.try_fill_resting(token, book, now)
            if fill is not None:
                pos = Position(
                    market=market, side=side,
                    entry_price=fill.price, size_shares=fill.size_shares,
                    entry_edge=fair.prob - fill.price, opened_at=now,
                )
                self.risk.register_open(token, pos)
            engaged = True
        return engaged

    def _handle_entries(self, market: Market, tick: Tick, now: float,
                        ratings: TeamRatings) -> None:
        if self._open_position_on(market) is not None:
            self._cancel_market_orders(market)  # position first: no stacked quotes
            return
        if self._manage_resting(market, tick, now, ratings):
            return
        for side in (Side.HOME, Side.AWAY):
            book = self._book_for(tick, side)
            if book.ask is None:
                continue
            fair = compute_fair_value(tick.state, side, self.cfg, ratings=ratings, now=now)
            size = self.risk.approve_size(book, fair, book.ask,
                                          self.broker.resting_count(),
                                          self.broker.resting_cost())
            sig = evaluate_entry(market, tick.state, book, fair, side, size, self.cfg, now)
            if sig is None:
                continue
            token = market.token_for(side)
            fill = self.broker.buy(token, side, sig.limit_price, sig.size_shares, book, sig.cross)
            if fill is not None:
                pos = Position(
                    market=market, side=side,
                    entry_price=fill.price, size_shares=fill.size_shares,
                    entry_edge=sig.fair - fill.price, opened_at=now,
                )
                self.risk.register_open(token, pos)
            if fill is not None or self.broker.resting_order(token) is not None:
                return  # filled or rested: one engagement per market per tick

    def _settle(self, market: Market, final: GameState) -> None:
        found = self._open_position_on(market)
        if found is None:
            return
        token, pos = found
        home_won = final.home_score > final.away_score
        won = home_won if pos.side is Side.HOME else not home_won
        self.broker.settle_resolution(token, pos.side, pos.size_shares, won)
        self._record_close(token, pos, 1.0 if won else 0.0, pos.size_shares,
                           ExitReason.RESOLVED, final.fetched_at)

    def _record_close(self, token: str, pos: Position, price: float, size: float,
                      reason: ExitReason, now: float) -> None:
        pnl = (price - pos.entry_price) * size
        self.result.trades.append(
            TradeRecord(
                game_pk=pos.market.game_pk, side=pos.side,
                entry_price=pos.entry_price, exit_price=price, size_shares=size,
                entry_edge=pos.entry_edge,
                entry_fair=pos.entry_price + pos.entry_edge,
                exit_reason=reason, opened_at=pos.opened_at, closed_at=now,
            )
        )
        if size >= pos.size_shares - 1e-9:
            self.risk.pop_position(token)
            self.risk.record_close(pnl)
        else:
            pos.size_shares -= size  # partial fill: keep the remainder open
            self.risk.record_close(pnl)

    def _mark_equity(self, tick: Tick) -> None:
        marks = 0.0
        for token, pos in self.risk.open_positions.items():
            book = self._book_for(tick, pos.side)
            if book.token_id == token and book.bid is not None:
                marks += book.bid * pos.size_shares
            else:
                marks += pos.cost
        equity = self.broker.cash + marks
        self._equity_peak = max(self._equity_peak, equity)
        if self._equity_peak > 0:
            dd = (self._equity_peak - equity) / self._equity_peak
            self.result.max_drawdown = max(self.result.max_drawdown, dd)


@dataclass
class HistoricalDataset:
    """M2 placeholder: a loader that yields (Market, Iterator[Tick]) per game,
    joining Polymarket CLOB price history with MLB play-by-play.

    Sources to wire:
      - CLOB price history REST endpoint (per token timeseries),
      - MLB Stats API historical live feeds (statsapi v1.1 supports replay),
      - alignment on wall-clock timestamps with a conservative lag assumption.
    """

    path: str

    def games(self) -> Iterator[Tuple[Market, Iterator[Tick]]]:
        raise NotImplementedError("historical loaders are an M2 deliverable (PRD §9)")
