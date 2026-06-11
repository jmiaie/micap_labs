# Architecture & Product Outline — `rallycap`

Companion to [`PRD.md`](./PRD.md) (requirements) and
[`STRATEGY_ASSESSMENT.md`](./STRATEGY_ASSESSMENT.md) (why the strategy looks
like this). This document maps requirements to components and code.

## 1. System overview

```
                 ┌────────────────────────────────────────────────┐
                 │                  rallycap bot                  │
                 │                                                │
 MLB Stats API ──┼─▶ feeds/mlb_statsapi ──┐                       │
 (game state)    │                        ▼                       │
                 │                  fair_value.py ◀── wp_model.py │
 Sharp odds ─────┼─▶ feeds/sharp_odds ────┘        (RE24 + normal │
 (optional)      │                        │         approx WP)    │
                 │                        ▼                       │
 Polymarket ─────┼─▶ feeds/polymarket ─▶ signals.py ─▶ risk.py    │
 Gamma + CLOB    │   (discovery, book)       │        (Kelly,     │
                 │                           ▼         caps, kill)│
                 │                    execution/broker            │
                 │                    ├─ PaperBroker (default)    │
                 │                    └─ PolymarketBroker (live)  │
                 │                           │                    │
                 │        state.json ◀── bot.py loop ──▶ trade log│
                 └────────────────────────────────────────────────┘
```

One process, one synchronous poll loop (default 2s tick) over all active
games. Deliberately boring: at seconds-scale latency targets, a single loop is
easier to make crash-safe than an async mesh. Upgrade path to `asyncio` +
CLOB websockets is isolated inside the feed classes (their public interfaces
are already snapshot-based).

## 2. Module map

```
rallycap/
├── docs/                       PRD, strategy assessment, this file
├── src/rallycap/
│   ├── config.py               All knobs from PRD §5 as a dataclass; JSON/env load
│   ├── types.py                GameState, BookTop, FairValue, Signal, Position, ...
│   ├── wp_model.py             In-game win probability (Lindsey-style normal
│   │                           approx + RE24 base-out adjustment + ratings)
│   ├── fair_value.py           Model/sharp blend, staleness & freeze logic
│   ├── ratings.py              pregame team priors implied from the pregame
│   │                           market price (consensus-anchored model)
│   ├── signals.py              Entry/exit decision functions (pure, testable)
│   ├── kelly.py                Kelly math + numeric multi-outcome optimizer
│   ├── risk.py                 Portfolio caps, daily kill switch, sizing approval
│   ├── feeds/
│   │   ├── teams.py            canonical MLB team table; identity matching for
│   │   │                       joining Stats API names to Gamma outcome labels
│   │   ├── polymarket.py       Gamma discovery + CLOB book client (httpx, lazy)
│   │   ├── mlb_statsapi.py     statsapi.mlb.com schedule + live feed parser
│   │   └── sharp_odds.py       SharpOddsSource protocol, de-vig math, Null impl
│   ├── execution/
│   │   └── broker.py           Broker protocol; PaperBroker (crossing fills +
│   │                           resting passive buys w/ trade-through fills and
│   │                           cancel-on-event); PolymarketBroker stub
│   ├── backtest/
│   │   ├── engine.py           Event-driven backtester + synthetic game/market sim
│   │   └── calibrate.py        WP-constant fitting from line scores + Brier gate
│   ├── bot.py                  Orchestrator loop, state persistence, modes
│   └── cli.py                  argparse CLI: discover / backtest / paper / live
└── tests/                      Unit tests for math + signals + backtest plumbing
```

### Dependency rule

`types.py ← {wp_model, kelly} ← {fair_value, signals, risk} ← {bot, backtest}`.
Strategy math imports **stdlib only** — it runs (and is tested) with no
network packages installed. `httpx` is imported lazily inside feed methods;
`py-clob-client` only inside `PolymarketBroker`. The backtester and the live
loop run the *same* signal/risk/broker code paths (PaperBroker), so a backtest
is a true rehearsal of the production logic.

## 3. Position lifecycle (state machine)

```
        edge ≥ τ + checks            filled                 exit signal
SCREEN ───────────────────▶ PENDING ───────▶ OPEN ──────────────────────▶ CLOSING ──▶ CLOSED
  ▲                            │  cancel-on-event /           │ (TP ≥ +50% │ salvage │
  │                            │  timeout / freeze            │  converge │ time stop)
  └────────────────────────────┘                              ▼
                                                   RESOLVED (game final: 0/1)
```

Every transition is journaled to `state.json` before the side effect is
confirmed, and reconciled against the venue on restart (F9, F7).

## 4. Tick pipeline (per 2s loop iteration)

1. Refresh game states for active gamePks; detect scoring events → start
   freeze windows; mark finals.
2. Refresh order books for tracked tokens; compute book age.
3. Compute `FairValue` per game (skip if any feed stale → stand down + cancel
   resting orders for that market).
4. For open positions: evaluate exit rules in PRD §5.4 priority order.
5. For screened markets without positions: evaluate entry rules (PRD §5.3);
   ask `risk.py` for an approved size (¼-Kelly ∧ caps ∧ depth).
6. Submit/cancel orders through the active `Broker`; journal; emit trade log
   records with predicted edge attached.
7. Update daily PnL; trip kill switch if breached (cancel all, flatten, halt).

## 5. Data contracts (defined in `types.py`)

- `GameState`: gamePk, inning, half, outs, bases occupied, score, final?,
  fetched_at, last_score_change_at.
- `BookTop`: bid/ask price+size, fetched_at; `spread`, `mid` helpers.
- `FairValue`: prob for the tracked side, model & sharp components, fresh flag.
- `Position`: token, side team, entry price/size, predicted edge at entry.
- `TradeRecord`: everything needed to compute calibration and edge
  realization offline (PRD F9, assessment §7).

## 6. Configuration

Single `BotConfig` dataclass (`config.py`) mirroring PRD §5 tables; loads from
JSON file with env-var overrides for secrets (`POLYMARKET_PRIVATE_KEY`, etc.).
The user's original strategy parameters are first-class config:
`underdog_price_band=(0.15, 0.30)`, `max_entry_inning=4`,
`take_profit_return=0.50`. Defaults are the PRD defaults; `mode="paper"`.

## 7. Testing strategy

- **Unit:** `wp_model` (monotonicity, symmetry, anchor points), `kelly`
  (closed-form vs numeric optimizer, caps), `signals` (each entry gate and
  exit priority), de-vig math.
- **Integration:** synthetic backtest end-to-end through real signal → risk →
  PaperBroker path; restart/reconciliation test on state.json (v1.1).
- **Statistical (M1+):** WP model Brier vs market on historical holdout;
  backtest with conservative fills; these are *gates*, not CI tests.

## 8. Failure handling

| Failure | Behavior |
|---|---|
| Game feed stale (> 10s) | Stand down market: cancel resting orders, no new entries, exits allowed at salvage only |
| Book feed stale | Same as above |
| Order reject / partial fill | Journal, re-evaluate next tick from actual position |
| Process crash | Restart → reload state.json → reconcile open orders/positions vs venue → resume |
| Daily kill switch | Cancel all, flatten at bid, halt until next UTC day; requires manual `--ack-kill` to resume same day |
| Venue/API errors | Exponential backoff per endpoint; global stand-down after N consecutive failures |

## 9. v2 directions (explicitly out of v1 scope)

Passive two-sided market making within the freeze framework (E4), CLOB
websockets, portfolio Kelly across simultaneous games, additional sports
(NBA in-game has deeper books but sharper competition), and a calibration
dashboard.
