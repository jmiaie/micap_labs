# rallycap ⚾

Polymarket MLB in-game trading bot. Watches games that turn lopsided early
(favorite implied 70–85%+), prices them independently with a win-probability
model, and trades the **mispriced** side — usually the trailing underdog —
when the market has overreacted. Exits on edge convergence with a hard +50%
take-profit cap; sizes with fractional Kelly under hard risk caps.

> **Read [`docs/STRATEGY_ASSESSMENT.md`](docs/STRATEGY_ASSESSMENT.md) first.**
> The naive version of this strategy ("buy any 15–30¢ underdog, sell +50%
> higher") is approximately zero-EV before costs and negative after — it wins
> ~⅔ of the time by construction while the losers exactly cancel the winners.
> This bot implements the *optimized* version: entries require measured
> mispricing against a fair-value model, not just a low price.

## Documents

| Doc | What it covers |
|---|---|
| [`docs/STRATEGY_ASSESSMENT.md`](docs/STRATEGY_ASSESSMENT.md) | Quantitative assessment of the raw strategy, where real edge lives, optimized entry/exit/sizing rules, validation gates |
| [`docs/PRD.md`](docs/PRD.md) | Product requirements: strategy parameters, functional requirements F1–F10, milestones M0–M5, risks |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design: components, data flow, position lifecycle, failure handling |

## Quickstart (no network, no keys)

```bash
cd rallycap
pip install -e ".[dev]"          # or just: pip install pytest

pytest                            # 37 unit tests
rallycap backtest --games 300     # synthetic end-to-end run
# (without install: PYTHONPATH=src python3 -m rallycap.cli backtest --games 300)
```

The synthetic backtest simulates games with *injected* market overreaction —
it validates the pipeline mechanics, **not** real-world profitability. Do not
tune parameters against it.

## Modes & lifecycle

```bash
rallycap discover                       # match today's MLB games to Polymarket markets
rallycap paper                          # live feeds, simulated fills (the default mode)
rallycap live --i-understand-live-risk  # real orders — gated until milestone M4
```

Lifecycle gates (PRD §9): scaffold → calibrate WP model on historical
play-by-play → historical backtest → ≥4 weeks paper with edge-realization
≥ 0.5 → micro-stakes live. Live order placement is intentionally stubbed
(`NotImplementedError`) until those gates pass.

## Configuration

All knobs from PRD §5 live in `src/rallycap/config.py` and load from a JSON
file (`rallycap --config my.json ...`). The original strategy survives as
first-class config:

```jsonc
{
  "underdog_price_band": [0.15, 0.30],  // the 70-85% favorite screen
  "max_entry_inning": 4,                // "early in the game"
  "take_profit_return": 0.50,           // automatic +50% take-profit (ON)
  "entry_edge": 0.05,                   // required mispricing vs fair value
  "kelly_fraction": 0.25,               // quarter-Kelly
  "max_trade_pct": 0.02,                // 2% bankroll cap per trade
  "daily_loss_limit_pct": 0.05          // kill switch
}
```

Secrets (live mode only) come from the environment — see `.env.example`.

## Repo layout

```
src/rallycap/
├── wp_model.py        in-game win probability (RE24 + normal approximation)
├── fair_value.py      model/sharp-book blend + freshness & freeze logic
├── signals.py         entry gates & exit priority (pure functions)
├── kelly.py           fractional Kelly sizing, multi-outcome optimizer
├── risk.py            caps, kill switch, size approval
├── feeds/             Polymarket Gamma/CLOB, MLB Stats API, team matching, sharp odds
├── execution/         PaperBroker (default) + gated PolymarketBroker
├── backtest/          event-driven engine + synthetic game simulator
├── bot.py             paper/live polling loop, persistence, trade log
└── cli.py             backtest / discover / paper / live
```

## Disclaimers

Trading prediction markets risks total loss of stake; nothing here is
financial advice. You are responsible for Polymarket ToS compliance and for
eligibility in your jurisdiction (US access runs through Polymarket's
CFTC-regulated entity; sports-contract rules are evolving). Start in paper
mode; the defaults assume you will.
