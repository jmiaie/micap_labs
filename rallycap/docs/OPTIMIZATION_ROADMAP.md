# Optimization Roadmap

Prioritized by expected impact on real-world profitability, with honest
status. Companion to [`STRATEGY_ASSESSMENT.md`](./STRATEGY_ASSESSMENT.md)
(why these and not others) and [`PRD.md`](./PRD.md) (milestone gates).

## Tier 1 — edge quality (where the money is)

| # | Optimization | Why it matters | Status |
|---|---|---|---|
| 1 | **Market-implied pregame ratings** (`ratings.py`) | With league-average priors, the model "disagrees" with the market about *team quality* and books phantom edges on every real favorite. Backing the strength gap out of the pregame price anchors the model to consensus, so in-game edge measures game-state disagreement only. | **Done (v0)** — implied from Polymarket pregame mid at discovery; mid-game joins fall back to league-average with a warning |
| 2 | **WP model calibration on historical play-by-play** | The four hand-set constants (`SIGMA_PER_HALF_INNING`, run rates, tie-break, RE24 vintage) are roughly right, not fitted. Calibration is the M1 gate: model Brier must match/beat the market's on a holdout. | Needs Retrosheet/Statcast data (network) — M1 |
| 3 | **Sharp-book anchor** (E2) | De-vigged Pinnacle-class live lines are the strongest in-play fair value; blending halves model error and catches what the model can't see (injuries, bullpen news). | Interface + de-vig shipped; needs odds-feed subscription decision (PRD open question 1) |
| 4 | **Power de-vig** (`devig_power`) | Multiplicative de-vig overstates longshot probability — in the exact tail this strategy trades. Power method loads vig removal onto the longshot, pulling the fair anchor toward reality. | **Done** — default for `devig_american` |
| 5 | **Per-event freeze with play-level detection** | Score-only freeze misses non-scoring repricings (bases-loaded jams, pitching changes, injury delays). Statsapi play events can drive finer freeze windows and cancel-on-event. | M1 — needs live-feed field verification |

## Tier 2 — execution & microstructure

| # | Optimization | Why it matters | Status |
|---|---|---|---|
| 6 | **Resting-order fills in paper/backtest** | The conservative "passive never fills" assumption understates the passive path; modeling queue position lets passive entries (which pay no spread) carry their weight in validation. Keep the conservative mode as the reporting default. | M2 — needs book-delta data to be honest |
| 7 | **CLOB websocket feeds** | Polling at 2s is fine for paper; live wants sub-second book staleness detection and faster cancel-on-event. | M1/M4 |
| 8 | **Tick-size & fee handling** | Sub-cent rounding and any venue fee schedule changes directly shrink 5¢ edges; encode per-market tick size and `fee_bps` from venue metadata instead of config defaults. | M1 — small |
| 9 | **Smart order placement** | Ladder passive bids across 2-3 levels inside the spread rather than a single price; improves fill rate per unit of adverse selection. | M4+, after passive-fill modeling |

## Tier 3 — risk & portfolio

| # | Optimization | Why it matters | Status |
|---|---|---|---|
| 10 | **Joint Kelly across simultaneous games** | Concurrent independent bets each deserve slightly smaller fractions than solo Kelly; at ¼-Kelly with a 2%/trade cap and a 10% at-risk cap the correction is third-order, which is why caps came first. | Deferred — revisit if caps ever loosen |
| 11 | **Edge-realization feedback loop** | Auto-shrink `kelly_fraction` when the rolling realized/predicted edge ratio degrades (model decay detector), instead of waiting for the human to notice. | M3 — needs paper-trade history |
| 12 | **Walk-forward parameter governance** | Re-fit thresholds (`entry_edge`, freeze window) on a rolling window with embargoed validation, never on the full sample. | M2 discipline, documented now |

## Explicitly not on the roadmap

- **Latency arms race** (colocating, sub-100ms feeds): the freeze window is
  the defense where we are slow; competing on speed against dedicated sports
  HFT on a venue this size has negative expected ROI.
- **More leagues by default**: MLB has exactly 30 teams (since 1998; a
  32-team expansion remains rumor as of 2026), and the team table covers all
  of them plus legacy aliases. Other baseball leagues (NPB, KBO, MiLB) are
  not listed on Polymarket as tradeable game markets; other *sports* (NBA
  in-game) are a v2 decision after MLB validates, per PRD non-goals.
- **Parameter tuning against the synthetic simulator** — it would tune
  against dynamics we injected ourselves (PRD risk table).
