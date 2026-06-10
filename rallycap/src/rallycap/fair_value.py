"""Fair value engine: blend the WP model with an optional sharp-book anchor,
and decide whether conditions are fresh enough to trade at all.

Freshness is a first-class output: a stale game feed or a just-scored run
means `fresh=False`, and the signal engine treats that as "stand down"
(highest adverse-selection moments). See STRATEGY_ASSESSMENT.md §6.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .config import BotConfig
from .types import FairValue, GameState, Side
from .wp_model import win_probability_for


@dataclass
class TeamRatings:
    """Pregame strength priors in runs above league average per 9 offensive
    innings. Defaults to zeros (league average); in practice supplied by
    ratings.py (implied from the pregame market price) or, at M1+, an
    external Elo/projection source."""

    home_rating_runs: float = 0.0
    away_rating_runs: float = 0.0


def compute_fair_value(
    state: GameState,
    side: Side,
    cfg: BotConfig,
    ratings: Optional[TeamRatings] = None,
    sharp_prob: Optional[float] = None,
    now: Optional[float] = None,
) -> FairValue:
    """Blended fair probability for `side`, with freshness verdict.

    `sharp_prob` is the de-vigged sharp-book probability for the same side
    (see feeds/sharp_odds.py), already side-aligned by the caller.
    """
    now = now if now is not None else time.time()
    ratings = ratings or TeamRatings()

    model_prob = win_probability_for(
        state, side, ratings.home_rating_runs, ratings.away_rating_runs
    )

    if sharp_prob is not None:
        w_m, w_s = cfg.model_weight, cfg.sharp_weight
        total = w_m + w_s
        prob = (w_m * model_prob + w_s * sharp_prob) / total
    else:
        prob = model_prob

    fresh = (
        not state.is_final
        and state.age_s(now) <= cfg.max_feed_age_s
        and state.seconds_since_score_change(now) >= cfg.freeze_seconds
    )

    return FairValue(
        side=side,
        prob=prob,
        model_prob=model_prob,
        sharp_prob=sharp_prob,
        fresh=fresh,
        computed_at=now,
    )
