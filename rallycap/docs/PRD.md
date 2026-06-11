# PRD — `rallycap`: Polymarket MLB In-Game Mean-Reversion Bot

**Owner:** Jeffrey Milam · **Status:** Draft v1.0 · **Date:** 2026-06-10

---

## 1. Summary

`rallycap` is an automated trading bot for Polymarket MLB game-winner markets.
It watches games that become lopsided early (favorite implied 70–85%+), prices
the game independently with an in-game win-probability model (optionally
anchored to sharp-book live lines), and trades the **mispriced** side — usually
the underdog — when the market has overreacted. Positions are sized with
fractional Kelly and exited on edge convergence, with a hard **+50%
take-profit** cap, a time stop, and a salvage exit.

The strategy rationale and the quantitative case for these design choices are
in [`STRATEGY_ASSESSMENT.md`](./STRATEGY_ASSESSMENT.md). The system design is
in [`ARCHITECTURE.md`](./ARCHITECTURE.md).

## 2. Goals

1. Systematically capture in-play overreaction in early-inning lopsided MLB
   games on Polymarket, with positive expected log growth of bankroll.
2. Be safe by construction: hard position caps, daily kill switch, feed
   staleness guards, and a paper mode that is the default.
3. Be measurable: every trade records predicted edge at entry so realized
   results can be compared against the model (edge realization ratio).
4. Support the full lifecycle: synthetic backtest → historical backtest →
   paper trading → micro-stakes live → scale.

## 3. Non-goals (v1)

- No sports other than MLB; no markets other than game winner (no run lines,
  totals, series, futures).
- No market making beyond passive entry orders (full two-sided MM is v2 — E4).
- No latency arms race: target is seconds-scale reaction, not sub-second.
- No portfolio optimization across correlated markets (game winners on
  different games are ~independent; caps are sufficient).
- No UI; CLI + logs + state file only.

## 4. Users

Single operator (the owner) running one instance against one Polymarket
account, on a small bankroll (capacity analysis: low thousands USDC max).

## 5. Strategy specification (the product's core requirements)

### 5.1 Universe screen

| Parameter | Default | Meaning |
|---|---|---|
| `sport` | MLB | Game-winner markets matched to MLB Stats API gamePk |
| `underdog_price_band` | [0.15, 0.30] | Favorite implied 70–85% |
| `max_entry_inning` | 4 | "Early in the game" |
| `min_book_depth_usd` | 50 | Displayed size at touch required to quote/take |
| `max_spread` | 0.04 | No trading into wider books |

### 5.2 Fair value

`fair = w_model · WP_model + w_sharp · WP_sharp` (weights renormalize when the
sharp feed is absent). `WP_model` is the in-game win-probability model (score
diff, inning/half/outs, base state via RE24, pregame ratings, home field).
Pregame ratings are implied from the pregame market price at discovery
(`ratings.py`), anchoring the model to consensus so in-game edge measures
game-state disagreement only; mid-game joins fall back to league-average
priors. `WP_sharp` is the de-vigged (power method) live moneyline from a
sharp odds source (optional adapter; v1 ships the interface plus a null
implementation).

### 5.3 Entry rules (ALL must hold)

| Rule | Default |
|---|---|
| Edge: `fair_side − ask ≥ entry_edge` | `entry_edge = 0.05` |
| Underdog screen passes (5.1) and game state fresh (`feed age ≤ 10s`) | — |
| Event freeze elapsed: ≥ `freeze_seconds` since last scoring change | 45 s |
| Risk module approves size (5.5) | — |
| Execution: passive limit at `min(ask − 0.01, fair − keep_edge)`; cross the spread only if `fair − ask ≥ entry_edge + spread_cost_margin` | `keep_edge = 0.03` |

The bot may take the *favorite* side under the same edge rules when the
mispricing is inverted (config `allow_favorite_side`, default true).

### 5.4 Exit rules (priority order, first match wins)

1. **Take-profit cap:** unrealized return ≥ `take_profit_return` (**0.50**,
   default ON — per original strategy requirement).
2. **Salvage:** model edge ≤ `−salvage_edge` (0.05) → exit at bid.
3. **Edge convergence:** bid ≥ `fair − exit_edge` (0.01) → exit.
4. **Time stop:** inning > `time_stop_inning` (7) → exit.
5. **Resolution:** game ends → position settles 0/1 (handled by venue).

No price-level stop-loss (see assessment §4).

### 5.5 Sizing & risk

| Parameter | Default |
|---|---|
| Kelly fraction of full Kelly `f* = (p − a)/(1 − a)` | 0.25 |
| Per-trade cap (% bankroll) | 2% |
| Per-trade cap vs displayed depth | ≤ 50% of touch size |
| Max concurrent positions | 5 |
| Max total at-risk (% bankroll) | 10% |
| Daily loss kill switch (% bankroll) | −5% → flat + halt until next day |
| Manual kill | SIGTERM/file flag → cancel all open orders, optionally flatten |

## 6. Functional requirements

- **F1 Market discovery.** Find today's MLB winner markets via Polymarket
  Gamma API; map each market to an MLB Stats API `gamePk` (team-name matching
  + date); refresh hourly and at startup.
- **F2 Game state feed.** Poll MLB Stats API live feed per active game
  (`poll_seconds`, default 2s): inning, half, outs, bases, score; detect
  scoring events (for freeze windows) and finals.
- **F3 Market data feed.** Poll/stream CLOB order book top + depth per market
  token; track book age.
- **F4 Fair value engine.** Compute blended fair (5.2) with staleness flags;
  expose components for logging.
- **F5 Signal engine.** Entry per 5.3, exits per 5.4, pure functions over
  (game state, book, fair, position, config) — fully unit-testable.
- **F6 Risk module.** Kelly sizing + all caps in 5.5; portfolio accounting;
  kill-switch state machine.
- **F7 Execution.** Broker abstraction with: `PaperBroker` (deterministic fill
  simulation) and `PolymarketBroker` (py-clob-client; EIP-712 signed orders;
  cancel-on-event; order/fill reconciliation on restart).
- **F8 Backtesting.** Event-driven engine consuming (a) synthetic game/book
  simulator with injectable overreaction (works offline, day one), (b)
  historical loaders: Polymarket CLOB price history + MLB play-by-play
  (schema defined; loaders are v1.1).
- **F9 Persistence & ops.** JSON state (bankroll, positions, order ids, daily
  PnL) surviving restart; structured trade log (entry edge, exit reason,
  realized PnL) for analysis; INFO console + JSONL file logs.
- **F10 Modes.** `backtest` (offline), `paper` (live feeds, simulated fills —
  **default**), `live` (real orders; requires `--i-understand-live-risk` flag
  AND env keys present).

## 7. Non-functional requirements

- **Latency budget:** feed poll ≤ 2s; signal→order < 500ms; cancel-on-event
  < 1s from score detection. (Seconds-scale by design; the freeze window is
  the defense where we are slow.)
- **Reliability:** crash-safe restart with order/position reconciliation
  against the venue; idempotent order submission (client order ids).
- **Security:** private key via env/`.env` only; never logged; live mode
  refuses to start if key file permissions are world-readable.
- **Testability:** strategy math (WP model, Kelly, signals) is pure-stdlib and
  unit-tested; network adapters thin and mockable.
- **Observability:** per-trade record includes model fair, sharp fair, edge,
  book snapshot, exit reason — enough to compute calibration and edge
  realization offline.

## 8. Compliance & account constraints

- Operator must be eligible to trade on Polymarket in their jurisdiction and
  comply with Polymarket ToS (API trading is officially supported; US access
  is via Polymarket's CFTC-regulated US entity). Geo/eligibility is the
  operator's responsibility; the bot ships with no circumvention features.
- Sports-event contracts are a fast-moving regulatory area in the US — verify
  current status of MLB markets for your account class before going live.
- Tax: trade log is the record; export is CSV-friendly JSONL.

## 9. Milestones

| | Deliverable | Gate to next |
|---|---|---|
| **M0** | Scaffold: docs, strategy/risk/execution modules, synthetic backtest, paper mode skeleton, tests (this repo) | Tests green; synthetic backtest runs |
| **M1** | Real feeds wired (Gamma/CLOB/StatsAPI); WP model calibrated on Retrosheet/Statcast | Brier ≤ market on holdout |
| **M2** | Historical backtest on Polymarket price history | +EV after conservative fills |
| **M3** | Paper trading ≥ 4 weeks | Edge realization ≥ 0.5 |
| **M4** | Live, micro-stakes ($1–5) | 2 weeks clean ops, PnL within model bands |
| **M5** | Scale to caps; consider E4 (passive MM) and v2 sports | — |

## 10. Risks

| Risk | Mitigation |
|---|---|
| Model miscalibration → false edges | M1/M2 gates; Brier benchmark vs market; sharp-anchor blend |
| Stale feed → picked off | Staleness guards, freeze windows, cancel-on-event |
| Thin books → can't exit at fair | Depth checks at entry; size ≤ 50% of touch; salvage exit accepts bid |
| Regulatory change on sports markets | Compliance check at startup is manual; kill switch; small bankroll |
| Key compromise | Env-only secrets, least-privilege proxy wallet, micro bankroll |
| Overfitting the synthetic simulator | Simulator is for plumbing only; no parameter tuning against it |

## 11. Open questions

1. Sharp odds source: The Odds API (paid, ~30s latency on live) vs none for
   v1 — does E3 alone clear the validation gates?
2. Polymarket fee schedule changes for sports markets — confirm current
   maker/taker fees before M4 and encode in `fee_bps` config.
3. Websocket vs polling for CLOB book data at M1 (polling is fine for paper).
