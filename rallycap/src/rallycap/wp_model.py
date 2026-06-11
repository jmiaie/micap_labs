"""In-game MLB win probability model (v0, parametric).

Approach (Lindsey 1961 lineage): the final run differential is approximated as
Normal with
  mean = current diff + expected runs from the rest of the game
         (per-inning scoring rates +/- team ratings + home field, plus the
          RE24 base-out run expectancy for the half-inning in progress), and
  variance proportional to the number of half-innings remaining.

This is deliberately simple, fully inspectable, and roughly right: pregame it
yields ~54% for an average home team, and it respects the leverage structure
of the game (late leads -> probabilities near 0/1). It is NOT yet calibrated.

CALIBRATION (M1, required before live):
  Fit `LEAGUE_RUNS_PER_INNING`, `SIGMA_PER_HALF_INNING`, `HOME_FIELD_RUNS`,
  and the tie-break term against Retrosheet/Statcast play-by-play, then verify
  Brier score vs market prices on a holdout (PRD M1 gate).
"""

from __future__ import annotations

import math

from .types import GameState, Half, Side

# Expected runs scored in the remainder of a half-inning, by (bases, outs).
# Standard RE24 matrix (league-average run environment, ~2010s values).
# Keyed by (on_first, on_second, on_third) -> [0 outs, 1 out, 2 outs].
RE24 = {
    (False, False, False): (0.481, 0.254, 0.098),
    (True, False, False): (0.859, 0.509, 0.224),
    (False, True, False): (1.100, 0.664, 0.319),
    (False, False, True): (1.350, 0.950, 0.353),
    (True, True, False): (1.437, 0.884, 0.429),
    (True, False, True): (1.784, 1.130, 0.478),
    (False, True, True): (1.964, 1.376, 0.580),
    (True, True, True): (2.292, 1.541, 0.752),
}

LEAGUE_RUNS_PER_INNING = 0.51      # ~4.6 runs / 9 innings per team
HOME_FIELD_RUNS = 0.22             # full-game home advantage in runs
SIGMA_PER_HALF_INNING = 0.74       # full-game margin std ~3.1 -> 3.1/sqrt(18)
HOME_TIE_WIN_PROB = 0.52           # home team edge in extras (bats last)
_MIN_SIGMA = 0.35                  # floor so late-game probs stay finite
_CLAMP = (0.001, 0.999)


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def expected_runs_rest_of_half_inning(state: GameState) -> float:
    bases = (state.on_first, state.on_second, state.on_third)
    outs = min(max(state.outs, 0), 2)
    return RE24[bases][outs]


def _remaining_offense_innings(state: GameState) -> tuple[float, float]:
    """(home, away) full innings of offense remaining AFTER the current
    half-inning, treating a tied/extra game as having one inning to play."""
    # Regulation frame: innings after the current one, floored at 0 for extras.
    after = max(0, 9 - state.inning)
    if state.half is Half.TOP:
        home = after + 1.0   # home still bats in the bottom of this inning
        away = float(after)
    else:
        home = float(after)
        away = float(after)
    if state.inning > 9 or (state.inning == 9 and home == 0.0 and away == 0.0):
        # Extras / bottom 9: model one more round each; tie-break term handles
        # the rest. Walk-off asymmetry is folded into HOME_TIE_WIN_PROB.
        home = max(home, 0.5)
        away = max(away, 0.5)
    return home, away


def win_probability(
    state: GameState,
    home_rating_runs: float = 0.0,
    away_rating_runs: float = 0.0,
) -> float:
    """P(home team wins) given the current game state.

    `*_rating_runs`: team strength as expected runs scored above league
    average per 9 innings of offense (e.g. +0.4 for a strong lineup). Pregame
    priors; supply 0.0 for league-average teams.
    """
    if state.is_final:
        return 1.0 if state.home_score > state.away_score else 0.0

    home_in, away_in = _remaining_offense_innings(state)
    home_rate = LEAGUE_RUNS_PER_INNING + (home_rating_runs + HOME_FIELD_RUNS) / 9.0
    away_rate = LEAGUE_RUNS_PER_INNING + away_rating_runs / 9.0

    mu = state.score_diff_home + home_in * home_rate - away_in * away_rate
    batting_re = expected_runs_rest_of_half_inning(state)
    mu += -batting_re if state.half is Half.TOP else batting_re

    half_innings_left = home_in + away_in + (1.0 - state.outs / 3.0)
    sigma = max(_MIN_SIGMA, SIGMA_PER_HALF_INNING * math.sqrt(max(half_innings_left, 0.25)))

    # Final margin M ~ N(mu, sigma^2): split the tie mass around zero.
    p_home_ahead = 1.0 - _phi((0.5 - mu) / sigma)
    p_tie = _phi((0.5 - mu) / sigma) - _phi((-0.5 - mu) / sigma)
    p = p_home_ahead + HOME_TIE_WIN_PROB * p_tie

    lo, hi = _CLAMP
    return min(hi, max(lo, p))


def win_probability_for(
    state: GameState,
    side: Side,
    home_rating_runs: float = 0.0,
    away_rating_runs: float = 0.0,
) -> float:
    p_home = win_probability(state, home_rating_runs, away_rating_runs)
    return p_home if side is Side.HOME else 1.0 - p_home


def pregame_win_probability(home_rating_runs: float = 0.0, away_rating_runs: float = 0.0) -> float:
    """Convenience: P(home wins) before first pitch."""
    state = GameState(
        game_pk=0, home_team="", away_team="",
        inning=1, half=Half.TOP, outs=0,
        on_first=False, on_second=False, on_third=False,
        home_score=0, away_score=0, is_final=False, fetched_at=0.0,
    )
    return win_probability(state, home_rating_runs, away_rating_runs)
