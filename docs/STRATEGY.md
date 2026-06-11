# Strategy: where edge actually lives in 5/15-minute BTC prediction markets

*Working document — June 2026. Numbers below are from the committed backtest
([results JSON](results/backtest_2025-06_2026-06.json)), 12 months fully
out-of-sample on real Bitstamp 1m data (Jun 2025 → Jun 11, 2026), purged
walk-forward, 54 weekly refits.*

## 1. The problem, stated honestly

"Predict BTC's next 5 minutes accurately" is not a thing public data can do.
At 5-minute horizons BTC close-to-close direction is ~98% noise: the best
bars-only models in the literature and in this repo's own walk-forward sit at
**51–53% directional accuracy** — statistically real (our p-values vs coin-flip
are 1e-22 and below across 108k non-overlapping windows), economically thin.

But the *contracts* we're trading are not "predict the future" — they're
**digital options quoted by other participants**, many of them slow or casual.
Beating a market is a different (easier) problem than beating the asset:
you need your probability to be *better calibrated than the quote*, after fees,
at the moment you trade. That reframing drives the whole design.

## 2. Venue mechanics (what we're actually pricing)

| | Polymarket "Bitcoin Up or Down" (5m/15m/1h) | Kalshi BTC (hourly ladders, 15m series) |
|---|---|---|
| payoff | YES iff close-of-window > open-of-window | YES iff BRTI above strike at expiry |
| settlement source | market-specified oracle (Chainlink/Pyth feeds) | CF Benchmarks BRTI (Bitstamp is a constituent) |
| fees | none on these series; cost = spread | taker fee ≈ **0.07·p·(1−p)** per contract (~1.75¢ at p=0.5) |
| typical state at open | ~50/50, thin book | ladder priced off spot ± vol |
| our fair value | `prob_window_up(spot, window_open, σ, τ)` | `prob_above(spot, strike, σ, τ)` |

Both reduce to one function: **P(S_T > ref)** under a driftless diffusion with
per-minute EWMA vol and a **fat-tailed standardized return distribution** fitted
empirically per minutes-remaining τ (BTC 1–5m returns are strongly non-normal;
the tails are exactly where binary quotes are most wrong).

## 3. The four edges, ranked by evidence

### E1 — Mid-window repricing speed (strongest, measured)
Inside a 5-minute window, fair P(up) moves **13.7¢/minute on average**
(7.6¢/min for 15m). Our pricer, repriced continuously from a websocket feed,
scored Brier **0.216 → 0.106** from minute 1 to minute 4 of 5m windows
(coin-flip = 0.25), with max calibration gap 2.6–3.9% and the P>0.9 bucket
realizing 90.5–96.6%. Any resting quote even ~30 seconds stale is mispriced by
several cents in expectation. This is a *latency + math* edge, not forecasting:
it monetizes whenever counterparties quote slower than the feed moves.
**This is the edge to build the live loop around.**

### E2 — Tail calibration (real, compounding with E1)
The empirical-Z pricer beats the normal-CDF version consistently (Brier
0.21639 vs 0.21677 at τ=4 … uniform across τ, largest in tails). Casual
traders price "can it move $300 in 90 seconds?" off intuition; the empirical
distribution knows. Cheap, durable, already implemented.

### E3 — ML direction tilt at window open (real, thin, venue-dependent)
Stacked logistic+HGB on 31 causal features: OOS 51.45% @5m / 51.36% @15m;
52.3–52.9% on the p≥0.55 confident subset. Simulated vs ~50/50 quotes with 1¢
half-spread: **+1.1 to +1.9¢ per $1** on Polymarket (fee-free), **negative to
breakeven on Kalshi** after its taker fee. Verdict: tradeable only on fee-free
venues, as a tilt on E1/E2 rather than standalone. Upside not yet captured:
the backtest source (Bitstamp) lacks taker-buy flow — Binance-trained flow
features (`flow_imb_*`, `cvd_slope`) are wired in and historically the
strongest microstructure family. Retrain on Binance data before judging E3's
ceiling.

### E4 — Kronos foundation model (implemented, unvalidated — next experiment)
[Kronos](https://github.com/shiyu-coder/Kronos) (24.7M-param K-line foundation
model) is integrated as a path-sampling ensemble member
(`KronosDirectionModel`): N sampled futures → smoothed P(up). Zero-shot it is
*not* presumed to add edge; the walk-forward harness exists to test it, and
`finetune_csv/` in the Kronos repo takes our parquet directly for BTC
fine-tuning. GPU recommended at live cadence.

### What we rejected (and why)
- **LLM agent swarms for the 5-minute horizon** (TradingAgents, ai-hedge-fund,
  MiroFish/sandfish patterns): all reviewed in depth. They reason over
  fundamentals/news/narratives at seconds-to-minutes latency with hours-old
  inputs — wrong domain at this cadence. What we *kept* is the architectural
  pattern: an ensemble of specialized signals (momentum/vol/flow/foundation-model)
  aggregated by a meta-learner with a risk gate, which is exactly
  `StackedDirectionModel` + `EdgeEngine`. LLM agents remain sensible for
  *event-horizon* markets (CPI prints, ETF flows, weekend gaps) — a later layer.
- **KratosMultiphysics**: finite-element multiphysics solver; no defensible
  mapping to market microstructure. Listed for completeness — not used.

## 4. System (what's built)

```
        Binance WS (spot, 1m klines)      Gamma/CLOB + Kalshi REST (quotes)
                 │                                   │
        SpotFeed (mid, EWMA σ, window opens)         │
                 │                                   │
   ┌─────────────┼────────────────┐                  │
   │ pricer: P(S_T>ref) empirical-Z │  ML stacker @ open │  Kronos (opt)
   └─────────────┼────────────────┘                  │
                 ▼                                   ▼
        blended fair value  ──────────►  EdgeEngine (fees, min-edge 3%,
                                          ¼-Kelly, 2% cap)
                                                 │
                                          Ledger (JSONL) ── settle vs feed,
                                          realized edge & oracle basis
```

Evaluation discipline baked in: features are mutation-tested for lookahead;
walk-forward purges ≥2×horizon at every boundary; calibration uses time-ordered
slices (Platt below 10k rows, isotonic above); significance is reported on
non-overlapping windows only; trade sims charge venue-specific fees + spread.

## 5. Risk & bankroll policy

- **Variance**: even +1.5¢/$1 edge at 52% hit rate draws down hard; sims show
  73–96% max drawdown at quarter-Kelly compounding without caps. Defaults:
  ¼-Kelly **and** 2% per-trade cap **and** 3% min net edge.
- **Adverse selection**: the 50/50-quote assumption is an upper bound — fills
  near fair value are likelier when you're wrong. Only live paper quotes
  (ledger) can measure this; that's why no order code ships until the ledger
  proves realized edge over ≥2 weeks.
- **Oracle basis**: we price off Binance; venues settle off Chainlink/Pyth/BRTI.
  Ledger records both; persistent basis ⇒ wire the venue's exact feed.
- **Regime risk**: weekly refits + drift monitoring via fold log; the no-signal
  test guards against confident hallucination.

## 6. Roadmap (priority order)

1. **Run the paper trader** against live Polymarket 5m/15m books for 2+ weeks;
   measure realized edge & oracle basis from the ledger. Gate everything on this.
2. **Binance retrain** with taker-buy flow features; re-run walk-forward; expect
   E3 to improve (flow imbalance is the strongest known family at this horizon).
3. **Kronos**: zero-shot walk-forward as third member; if promising, fine-tune
   on BTC 1m via `finetune_csv`.
4. Quote-capture daemon: store venue books each second → replace the assumed-
   quote trade sim with replayed real quotes (the last honesty gap).
5. Near-expiry vol refinement (τ<2min: blend 5m-halflife vol; fixes the 0.08
   calib gap at 15m/τ=1) and event-calendar no-trade gate (CPI/FOMC minutes).
6. Maker-side execution study for Kalshi (fee asymmetry flips E3 sign if we
   can rest orders).

## 7. Resource repos — final verdicts

| repo | verdict |
|---|---|
| shiyu-coder/Kronos | **Integrated** (adapter + fine-tune path); validation pending |
| ff137/bitstamp-btcusd-minute-data | **Adopted** as backtest data source (MIT, daily-updated) |
| TauricResearch/TradingAgents | Pattern donor (ensemble→risk-gate flow); too slow for 5m loop |
| virattt/ai-hedge-fund | Pattern donor (signal aggregation); wrong horizon/domain |
| 666ghj/MiroFish, jmiaie/sandfish | Narrative/social simulation engines — candidates for *event-market* layer later, not 5m BTC |
| KratosMultiphysics/Kratos | Not applicable (FEM solver) |
