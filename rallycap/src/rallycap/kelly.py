"""Kelly criterion sizing for binary-market positions.

Why Kelly and not "compound the +50% wins": some positions resolve to zero,
and reinvesting aggressively into a return stream containing -100% outcomes
drives long-run log growth to -infinity. Fractional Kelly maximizes long-run
growth subject to survival. See STRATEGY_ASSESSMENT.md §5.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence, Tuple

# An outcome is (net_return_per_dollar_staked, probability).
Outcome = Tuple[float, float]


def kelly_binary(p: float, price: float) -> float:
    """Full-Kelly fraction for buying a binary share at `price` with true win
    probability `p`, held to resolution: win pays (1-price)/price per $1
    staked, loss is -1. Closed form: f* = (p - price) / (1 - price).
    Returns 0 when there is no edge.
    """
    if not (0.0 < price < 1.0):
        return 0.0
    return max(0.0, (p - price) / (1.0 - price))


def expected_log_growth(f: float, outcomes: Iterable[Outcome]) -> float:
    """E[log(1 + f*R)] for stake fraction f over discrete outcomes."""
    total = 0.0
    for net_return, prob in outcomes:
        wealth = 1.0 + f * net_return
        if wealth <= 0.0:
            return -math.inf
        total += prob * math.log(wealth)
    return total


def optimal_fraction(outcomes: Sequence[Outcome], tol: float = 1e-6) -> float:
    """Numeric Kelly for an arbitrary discrete outcome distribution via
    golden-section search on the (concave) expected log growth."""
    psum = sum(p for _, p in outcomes)
    if abs(psum - 1.0) > 1e-6:
        raise ValueError(f"outcome probabilities sum to {psum}, expected 1")
    ev = sum(r * p for r, p in outcomes)
    if ev <= 0.0:
        return 0.0

    worst = min(r for r, _ in outcomes)
    hi = 0.999 if worst >= 0 else min(0.999, -0.999 / worst)  # keep wealth > 0
    invphi = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = 0.0, hi
    c, d = b - invphi * (b - a), a + invphi * (b - a)
    fc, fd = expected_log_growth(c, outcomes), expected_log_growth(d, outcomes)
    while b - a > tol:
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - invphi * (b - a)
            fc = expected_log_growth(c, outcomes)
        else:
            a, c, fc = c, d, fd
            d = a + invphi * (b - a)
            fd = expected_log_growth(d, outcomes)
    return (a + b) / 2.0


def trade_outcomes(
    entry_price: float,
    fair: float,
    take_profit_return: float,
    converge_exit_price: float,
    gap_loss_prob: float = 0.15,
) -> list[Outcome]:
    """EV-consistent three-channel model of the optimized trade shape, for
    sizing under the convergence-first exit policy:

      - dead:      price collapses to ~0 before any exit fires (-100%). This is
                   NOT (1 - fair): under early exits, most losing-resolution
                   paths sold at convergence long before the loss. Only the
                   pre-exit gap (a big inning before the market converges)
                   lands here; `gap_loss_prob` is that probability.
      - take-profit: a favorable jump carries the bid past the +TP cap.
      - converge:  the workhorse exit at `converge_exit_price` (~ fair - eps).

    Channel probabilities are chosen so the expected exit price equals `fair`
    (optional stopping on the post-relaxation martingale), clamped
    conservatively: if the TP cap truncates so hard that `fair` is
    unreachable, the shape simply carries less EV than the entry edge implies.
    """
    if not (0.0 < entry_price < 1.0):
        raise ValueError("entry_price must be in (0,1)")
    if not (0.0 <= gap_loss_prob < 1.0):
        raise ValueError("gap_loss_prob must be in [0,1)")
    tp_price = min(0.999, entry_price * (1.0 + take_profit_return))
    s = min(converge_exit_price, tp_price)  # converge exit can't exceed the cap
    survive = 1.0 - gap_loss_prob

    if tp_price > s:
        p_tp = (fair - survive * s) / (tp_price - s)
        p_tp = min(max(p_tp, 0.0), survive)
    else:
        p_tp = survive  # cap below convergence level: everything exits at cap
    p_conv = survive - p_tp
    return [
        ((tp_price - entry_price) / entry_price, p_tp),
        ((s - entry_price) / entry_price, p_conv),
        (-1.0, gap_loss_prob),
    ]


def recommended_fraction(
    entry_price: float,
    fair: float,
    take_profit_return: float,
    converge_exit_price: float,
    kelly_fraction: float,
    gap_loss_prob: float = 0.15,
) -> float:
    """Conservative sizing: min(closed-form binary Kelly, numeric Kelly on the
    realistic exit shape) scaled by the configured Kelly fraction.

    The exit-shape Kelly is usually the larger of the two (early exits trim
    the -100% tail, tolerating more size); taking the min means we never let
    modeled exit discipline justify a bigger bet than hold-to-resolution
    would — feeds fail and exits sometimes can't fire.
    """
    f_binary = kelly_binary(fair, entry_price)
    f_shape = optimal_fraction(
        trade_outcomes(entry_price, fair, take_profit_return, converge_exit_price, gap_loss_prob)
    )
    return kelly_fraction * min(f_binary, f_shape)
