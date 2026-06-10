"""Sharp-book fair value anchor (strategy assessment E2).

De-vigged live moneylines from a sharp source are the strongest available
in-play fair value. v1 ships the interface, the de-vig math, and a Null
implementation; wiring a real source (e.g. The Odds API live endpoints,
~$, ~30s latency) is an M1 decision — see PRD open question 1.
"""

from __future__ import annotations

from typing import Optional, Protocol


class SharpOddsSource(Protocol):
    def live_home_prob(self, game_pk: int) -> Optional[float]:
        """De-vigged P(home win) for the game, or None if unavailable/stale."""
        ...


class NullSharpOdds:
    """No sharp feed: fair value falls back to the WP model alone."""

    def live_home_prob(self, game_pk: int) -> Optional[float]:
        return None


def american_to_implied(odds: int) -> float:
    """American odds -> implied probability (vig included)."""
    if odds == 0:
        raise ValueError("american odds cannot be 0")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


def devig_two_way(implied_a: float, implied_b: float) -> tuple[float, float]:
    """Multiplicative (proportional) de-vig of a two-way market.

    The simplest standard method; power/Shin de-vigging better handles
    favorite-longshot skew and is a worthwhile M1 upgrade given this strategy
    lives exactly in the skewed tail.
    """
    total = implied_a + implied_b
    if total <= 0:
        raise ValueError("implied probabilities must be positive")
    return implied_a / total, implied_b / total


def devig_american(home_ml: int, away_ml: int) -> tuple[float, float]:
    """American moneylines -> de-vigged (p_home, p_away)."""
    return devig_two_way(american_to_implied(home_ml), american_to_implied(away_ml))
