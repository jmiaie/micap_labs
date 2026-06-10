"""Market-implied pregame team ratings.

The WP model needs pregame team-strength priors; with the default zeros it
assumes every game is league-average vs league-average, so any game with a
real favorite would show a phantom "edge" the moment the market (correctly)
prices team quality. Rather than maintain an external Elo/projection feed
(an M1+ option), v0 backs the strength gap OUT of the pregame market price:
solve for the rating difference that makes the model's pregame probability
equal the market's.

The in-game model is thereby anchored to pregame consensus, and in-game edge
measures disagreement about the GAME STATE only — the largest source of
false entry signals for an uncalibrated model. (Options analogy: imply the
vol from the market, then price relative moves off it.)

Caveat: this trusts the pregame price. If the pregame market itself is mad,
the anchor inherits it — acceptable, because the strategy's thesis is
in-game overreaction, not pregame mispricing.
"""

from __future__ import annotations

from .fair_value import TeamRatings
from .wp_model import pregame_win_probability

_D_RANGE = (-5.0, 5.0)        # rating gap search bounds, runs/9 (huge: ~.250 win% gap)
_PROB_CLAMP = (0.15, 0.85)    # sane pregame MLB range; beyond this, clamp


def implied_rating_diff(pregame_home_prob: float, tol: float = 1e-4) -> float:
    """Home-minus-away strength gap d (runs per 9 offensive innings, beyond
    home field) such that the WP model's pregame P(home) equals the market's.
    Bisection; pregame probability is strictly increasing in d."""
    lo_p, hi_p = _PROB_CLAMP
    target = min(max(pregame_home_prob, lo_p), hi_p)
    lo, hi = _D_RANGE
    if pregame_win_probability(lo / 2, -lo / 2) >= target:
        return lo
    if pregame_win_probability(hi / 2, -hi / 2) <= target:
        return hi
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        if pregame_win_probability(mid / 2, -mid / 2) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def ratings_from_pregame_prob(pregame_home_prob: float) -> TeamRatings:
    """Symmetric split of the implied gap into home/away ratings."""
    d = implied_rating_diff(pregame_home_prob)
    return TeamRatings(home_rating_runs=d / 2.0, away_rating_runs=-d / 2.0)
