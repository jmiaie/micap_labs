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

    Simple and standard, but it spreads the vig evenly — empirically the
    bookmaker loads more vig onto the longshot (favorite-longshot bias), so
    this method *overstates* longshot probability. Prefer `devig_power` for
    fair-value anchoring; this is kept for comparison and tests.
    """
    total = implied_a + implied_b
    if total <= 0:
        raise ValueError("implied probabilities must be positive")
    return implied_a / total, implied_b / total


def devig_power(implied_a: float, implied_b: float, tol: float = 1e-10) -> tuple[float, float]:
    """Power-method de-vig: find k with a^k + b^k = 1, fair probs (a^k, b^k).

    Loads relatively more of the vig removal onto the longshot, matching the
    documented favorite-longshot skew — which is exactly the tail this
    strategy trades, so the de-vig choice materially moves the fair anchor.
    f(k) = a^k + b^k is strictly decreasing for a, b in (0,1): bisection.
    """
    if not (0.0 < implied_a < 1.0 and 0.0 < implied_b < 1.0):
        raise ValueError("implied probabilities must be in (0,1)")
    lo, hi = 0.01, 1.0
    while implied_a**hi + implied_b**hi > 1.0:
        lo, hi = hi, hi * 2.0
        if hi > 128.0:
            raise ValueError("power de-vig failed to bracket; degenerate inputs")
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        if implied_a**mid + implied_b**mid > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    return implied_a**k, implied_b**k


def devig_american(home_ml: int, away_ml: int) -> tuple[float, float]:
    """American moneylines -> de-vigged (p_home, p_away), power method."""
    return devig_power(american_to_implied(home_ml), american_to_implied(away_ml))
