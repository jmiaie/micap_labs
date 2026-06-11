"""CLI entry point.

  rallycap backtest --games 200 --seed 7     # offline synthetic run (no network)
  rallycap calibrate --line-scores g.jsonl   # fit WP model constants from data
  rallycap discover                          # list today's matched markets
  rallycap paper                             # live feeds, simulated fills
  rallycap live --i-understand-live-risk     # real orders (gated until M4)
"""

from __future__ import annotations

import argparse
import logging
import sys

from .backtest.engine import BacktestEngine, SimParams
from .bot import Bot
from .config import BotConfig


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rallycap", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="path to JSON config (see docs/PRD.md §5)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", help="synthetic backtest (offline)")
    bt.add_argument("--games", type=int, default=200)
    bt.add_argument("--seed", type=int, default=7)
    bt.add_argument("--overreaction", type=float, default=SimParams.overreaction,
                    help="simulated post-event market overshoot (prob units)")

    cal = sub.add_parser("calibrate", help="fit WP model constants from line scores")
    cal.add_argument("--line-scores", required=True,
                     help="JSONL of {\"home\": [runs/inning], \"away\": [...]} per game "
                          "(see backtest/calibrate.py for schema and sources)")

    sub.add_parser("discover", help="match today's MLB games to Polymarket markets")
    sub.add_parser("paper", help="run with live feeds and simulated fills")

    live = sub.add_parser("live", help="run with real orders (M4 gate)")
    live.add_argument("--i-understand-live-risk", action="store_true")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    cfg = BotConfig.load(args.config)

    if args.command == "backtest":
        cfg.mode = "backtest"
        engine = BacktestEngine(cfg)
        params = SimParams(overreaction=args.overreaction)
        result = engine.run_synthetic(n_games=args.games, seed=args.seed, params=params)
        print(result.summary())
        print("\nNOTE: synthetic games contain injected overreaction by construction —")
        print("this validates the pipeline, not the edge (docs/STRATEGY_ASSESSMENT.md §7).")
        return 0

    if args.command == "calibrate":
        from .backtest.calibrate import calibrate, load_line_scores_jsonl

        report = calibrate(load_line_scores_jsonl(args.line_scores))
        print(report.summary())
        return 0

    if args.command == "discover":
        bot = Bot(cfg=cfg)
        for tg in bot.discover():
            m = tg.market
            print(f"gamePk {m.game_pk}: {m.away_team} @ {m.home_team} (condition {m.condition_id})")
        return 0

    if args.command == "paper":
        cfg.mode = "paper"
        Bot(cfg=cfg).run()
        return 0

    if args.command == "live":
        if not args.i_understand_live_risk:
            print("live mode requires --i-understand-live-risk", file=sys.stderr)
            return 2
        if not cfg.polymarket_private_key:
            print("live mode requires POLYMARKET_PRIVATE_KEY in the environment", file=sys.stderr)
            return 2
        cfg.mode = "live"
        Bot(cfg=cfg).run()
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
