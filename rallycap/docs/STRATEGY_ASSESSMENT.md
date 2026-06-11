# Strategy Assessment: MLB In-Game Underdog Mean-Reversion on Polymarket

**Status:** v1.0 — quantitative review of the proposed strategy, and the optimized
specification that the bot actually implements.

---

## 1. The strategy as proposed

> Find MLB games early in the game where odds heavily favor one side (70–85%+).
> Buy the trailing team's shares. Exit as the position improves into the 40–60%
> range. Automatically take profit at anything over +50% because compounding at
> that rate would be powerful.

Formalized:

- **Universe:** in-game MLB winner markets, innings 1–4, favorite implied
  probability 0.70–0.85 → underdog shares priced `a ∈ [0.15, 0.30]`.
- **Entry:** buy underdog YES at ask `a`, unconditionally on price level.
- **Exit:** sell when share price ≥ `1.5·a` (+50% return), or hold to resolution.

## 2. Honest verdict on the raw strategy: ≈ zero EV before costs, negative after

### 2.1 Exit rules cannot manufacture expected value

A Polymarket binary share price is (to first order) the market's estimate of the
win probability. Under no-arbitrage, the in-play price is approximately a
**martingale**: its expected future value equals its current value. The optional
stopping theorem then says **every** exit rule — take-profit at +50%, stop-loss,
time stop, any combination — has expected value zero before transaction costs.
You cannot trade your way to positive EV with exits alone; the EV of a trade is
fixed at entry by `(true probability − price paid)`.

### 2.2 The win-rate illusion (why this strategy *feels* like it works)

For a continuous martingale starting at `a` with absorbing resolution, the
probability of touching a take-profit barrier `b = 1.5a` before the game ends is
approximately `a/b = 2/3`. So the raw strategy wins roughly **67% of the time**:

```
EV = (2/3)·(+0.50a) + (1/3)·(−1.00a) = +0.333a − 0.333a = 0
```

A 67% win rate with zero expectation is exactly the profile that produces
confident discretionary traders and steadily shrinking bankrolls. The one-third
of trades that lose, lose everything, and they precisely cancel the winners.

### 2.3 Costs make it strictly negative

- **Spread/slippage:** in-game MLB books on Polymarket commonly show 1–3¢
  spreads with thin top-of-book depth. Crossing the spread both ways on a 20¢
  position costs 5–15% of stake per round trip.
- **Adverse selection:** your resting orders get filled fastest at the exact
  moment someone with a lower-latency score feed knows the price is wrong.

Raw strategy expectation: **≈ −5% to −15% of stake per trade.**

### 2.4 The behavioral evidence points the wrong way

The intuition "they were so poorly favored, they only need to come back a
little" treats price like a stretched rubber band. Price is a probability, not a
spring — a 20¢ team really does lose ~80% of the time, and a comeback to 40¢ is
already reflected in the 20¢ you paid for the chance of it.

Worse, the documented bias in sports markets is the **favorite–longshot bias**:
recreational money systematically *overpays* for longshots (Thaler & Ziemba
1988; Tetlock 2004 found exactly this in in-game sports contracts on
TradeSports, Polymarket's closest historical analogue). Buying 15–30¢ underdogs
indiscriminately doesn't harvest a bias — it *pays* one.

Finally, baseball win probability moves in **jumps** (a three-run homer is a
10–20 point repricing). Jumps degrade the barrier math: the path can leap from
22¢ to 8¢ without ever offering you an exit, so the realized win rate at a 1.5×
target lands below the continuous 67% estimate while the loss size stays −100%.

**Conclusion: do not trade the raw rule. But the *shape* of the trade is
salvageable — the fix is to make entries conditional on measured mispricing
rather than on price level.**

---

## 3. Where real edge can exist (and how the bot is restructured to capture it)

The raw rule's universe — early-inning games that just became lopsided — is
actually a *good place to look* for mispricing, because that is when in-play
books are most emotional and most stale. Four harvestable effects:

**E1. Overreaction to scoring events.** Thin, retail-heavy in-play books
overshoot after big plays. A 3-run first inning against a strong team often
moves the market further than a calibrated win-probability model justifies.
This is the legitimate kernel of your "comeback" intuition: not that trailing
teams come back, but that the market *overprices the deficit* in the heat of
the moment. Note this cuts both ways — sometimes the mispriced side is the
**favorite** after a cheap scare, and the bot should be willing to take either
side of the same screen.

**E2. Lead–lag versus sharp reference prices.** De-vigged live moneylines from
sharp bookmakers (Pinnacle-class) and exchange feeds are the de facto fair
value for MLB in-play. Polymarket can lag them by seconds to minutes during
action. `fair − polymarket_ask > threshold` is a classic lead–lag signal.

**E3. Model-based mispricing.** An in-game win probability model — score
differential, inning/outs, base state (RE24 run expectancy), pregame team
strength, home field — calibrated on historical play-by-play, flags quotes that
deviate from fundamentals even without a sharp feed.

**E4. Liquidity provision.** Instead of *crossing* a wide spread, rest bids
below fair value on the underdog side. You collect the spread others pay, and
only acquire inventory at prices better than fair — provided quotes are pulled
instantly when a play starts or feeds go stale (cancel-on-event), which is the
standard defense against being picked off.

### The reframe

| | Raw strategy | Optimized strategy |
|---|---|---|
| 70–85% favorite, innings 1–4 | **Trigger** (buy because price is low) | **Universe screen** (where to look) |
| Entry | Unconditional at price level | Only when `edge = fair − ask ≥ τ_entry` after costs, with depth/spread/freshness checks |
| Side | Always the underdog | Whichever side is mispriced (usually the dog in this screen, not always) |
| Exit | +50% take-profit | Edge convergence first; +50% TP retained as a cap; time stop; salvage exit |
| Sizing | Implicit full compounding | Fractional Kelly with hard caps |

---

## 4. Optimized exit policy

EV is created at entry; **exits only shape its distribution**. Priority order:

1. **Take-profit cap (kept from your spec, default ON):** unrealized return
   ≥ +50% → exit. Under positive-edge entries this costs a little tail EV and
   buys a big variance reduction; it is cheap insurance, but it is *not* the
   edge.
2. **Salvage:** model edge has flipped negative beyond `τ_salvage` (the market
   knows something / the thesis broke) → exit at bid, even at a loss.
3. **Edge convergence (the workhorse):** bid ≥ `fair − τ_exit` → thesis
   realized, exit. Buying at 24¢ with fair 30¢ typically books **+15–30%**
   here, *before* the +50% cap would ever trigger. Expect most winners to be
   convergence exits, not TP exits.
4. **Time stop:** end of inning 7 (configurable). The thesis is early-game
   overreaction; late innings are a different, jumpier regime where a small
   price move is a huge probability move.
5. **No price-level stop-loss.** In binary markets a naive stop converts jump
   noise into systematically realized losses (you sell the bottom of every
   spike). Downside is controlled by *position sizing*, not stops.

## 5. Sizing: the compounding claim, corrected

"Take profit at +50% because compounding at that rate is powerful" has a fatal
flaw: some positions resolve to **zero**. Reinvesting aggressively into a return
stream that includes −100% outcomes drives long-run log growth to −∞ — one bad
day ends the bankroll. The correct frame is **Kelly**:

- Binary share at price `a` with true probability `p`: full Kelly fraction
  `f* = (p − a)/(1 − a)`.
- Example: model says `p = 0.30`, ask `a = 0.24` → `f* ≈ 7.9%` of bankroll.
  At the configured **¼-Kelly**: ≈ 2.0%.
- Defaults: ¼-Kelly multiplier, **2% bankroll cap per trade**, **5 concurrent
  positions / 10% total at-risk cap**, **−5% daily loss kill switch** (halts
  the bot for the day).

Realistic arithmetic at this sizing: a true 4–6¢ edge on a ~22¢ entry with
conservative (≈50%) edge realization ≈ **+8–12% of stake per trade**, i.e.
**+15–25bp of bankroll per trade**, across 2–6 qualifying trades on a full
slate. Compounding comes from *many small positive-EV trades at survivable
size* — that is the powerful version of your compounding idea.

**Capacity warning:** in-play MLB top-of-book depth on Polymarket is often only
$50–$500. This strategy is viable for a small bankroll and does not scale far;
size caps in config reflect displayed depth, not just bankroll.

## 6. Execution discipline (where most of the edge is won or lost)

- **Passive-first:** post limit orders inside the spread at `fair − edge_keep`;
  cross only when measured edge exceeds the crossing cost with margin.
- **Event freeze:** no quoting/taking for `freeze_seconds` (default 45s) after
  any scoring play or feed gap — the highest adverse-selection window. Resting
  orders are cancelled on event detection (cancel-on-event).
- **Staleness guard:** if the game feed or book feed is older than
  `max_feed_age_s`, the bot stands down. Trading on a stale feed against
  faster counterparties is the canonical way bots like this die.

## 7. Validation gates (must pass before risking capital)

1. **Backtest** on historical Polymarket in-game prices (CLOB price history)
   joined to MLB play-by-play; walk-forward, conservative fill model (fills at
   touch only up to displayed size; assume worst queue position).
2. **Model calibration:** the WP model's Brier score on the traded subset must
   beat or match the market's. If the model can't out-predict the price, E3 is
   off and only E2/E4 (sharp anchor, liquidity provision) are licensed.
3. **Paper trading ≥ 4 weeks** with live feeds: measure **edge realization
   ratio** (realized PnL ÷ predicted edge at entry). Below ~0.5 → recalibrate
   before going live.
4. **Live at micro-stakes** ($1–5 positions) before any scale-up.

## 8. Verdict

| | Expectation | Win rate | Failure mode |
|---|---|---|---|
| **Raw strategy** | ≈ 0 pre-cost; **−5–15%/trade after costs** | 60–67% (illusory) | Longshot bias + spread bleed + jump risk |
| **Optimized strategy** | Plausibly **+3–8% of stake/trade** after costs, *conditional on validation gates* | ~55–65% with small winners, sized losers | Stale feeds, model miscalibration, thin capacity |

The bot in this repo implements the optimized strategy. Your original
parameters survive as configuration: the 70–85% screen (`underdog_price_band`),
early-inning window (`max_entry_inning`), and the +50% take-profit
(`take_profit_return`, default on) — but entries require measured edge, sizing
is fractional Kelly, and exits are convergence-first.

## 9. References

- Thaler & Ziemba (1988), "Parimutuel betting markets: racetracks and
  lotteries" — favorite–longshot bias.
- Tetlock (2004), "How efficient are information markets? Evidence from an
  online exchange" — overpricing of low-probability in-game sports outcomes.
- Lindsey (1961), "The progress of the score during a baseball game" — basis
  for normal-approximation win probability models.
- Croxson & Reade (2014), "Information and efficiency: goal arrival in soccer
  betting" — speed of in-play repricing on liquid exchanges (the bar your
  latency must clear).
