from rallycap.backtest.engine import BacktestEngine, SimParams, SyntheticGame
from rallycap.config import BotConfig
from rallycap.types import Half

import random


def test_synthetic_game_produces_coherent_ticks():
    game = SyntheticGame(game_pk=1, rng=random.Random(3), params=SimParams())
    ticks = list(game.ticks())
    assert len(ticks) > 40                       # at least ~9 innings of outs
    final = ticks[-1].state
    assert final.is_final
    assert final.home_score != final.away_score  # baseball has no draws
    for t in ticks:
        assert 0 <= t.state.outs <= 2
        assert t.book_home.bid < t.book_home.ask
        # two tokens of one market: prices must be complementary-ish
        assert abs(t.book_home.mid + t.book_away.mid - 1.0) < 0.05


def test_backtest_runs_and_trades():
    cfg = BotConfig(bankroll_usd=1000.0)
    engine = BacktestEngine(cfg)
    result = engine.run_synthetic(n_games=120, seed=11)
    assert result.games == 120
    assert result.n_trades > 5                   # the screen does fire sometimes
    assert result.ending_bankroll > 0            # sizing prevents ruin
    assert result.max_drawdown < 0.5
    # Every trade carries the analysis fields the strategy gates depend on.
    for t in result.trades:
        assert t.entry_edge > 0
        assert 0.0 < t.entry_price < 1.0
    # With injected overreaction the pipeline should harvest positive PnL;
    # this asserts plumbing (entries find the synthetic edge, exits realize
    # it), NOT real-world profitability — see engine docstring.
    assert result.total_pnl > 0


def test_backtest_respects_kill_switch_state():
    cfg = BotConfig(bankroll_usd=1000.0, daily_loss_limit_pct=0.000001)
    engine = BacktestEngine(cfg)
    result = engine.run_synthetic(n_games=30, seed=5)
    # With an absurdly tight daily loss limit, the kill switch must engage on
    # any losing game-day rather than letting losses run.
    assert engine.risk.killed or result.total_pnl >= 0
