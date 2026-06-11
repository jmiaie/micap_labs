import math

import pytest

from rallycap.kelly import (
    expected_log_growth,
    kelly_binary,
    optimal_fraction,
    recommended_fraction,
    trade_outcomes,
)


def test_kelly_binary_closed_form():
    # p=0.30, price=0.24 -> (0.30-0.24)/0.76
    assert kelly_binary(0.30, 0.24) == pytest.approx(0.06 / 0.76)


def test_kelly_binary_no_edge_is_zero():
    assert kelly_binary(0.24, 0.24) == 0.0
    assert kelly_binary(0.20, 0.24) == 0.0  # negative edge never sized


def test_optimizer_matches_closed_form_binary():
    p, price = 0.35, 0.25
    outcomes = [((1 - price) / price, p), (-1.0, 1 - p)]
    assert optimal_fraction(outcomes) == pytest.approx(kelly_binary(p, price), abs=1e-4)


def test_optimizer_zero_for_fair_coin():
    outcomes = [(1.0, 0.5), (-1.0, 0.5)]
    assert optimal_fraction(outcomes) == 0.0


def test_full_stake_with_total_loss_outcome_ruins():
    # The "compound everything" fallacy: any -100% outcome makes f=1 -> -inf growth.
    outcomes = [(0.5, 0.9), (-1.0, 0.1)]
    assert expected_log_growth(1.0, outcomes) == -math.inf
    assert optimal_fraction(outcomes) < 1.0


def test_trade_outcomes_are_ev_consistent_with_fair():
    entry, fair = 0.24, 0.30
    outs = trade_outcomes(entry, fair, take_profit_return=0.50,
                          converge_exit_price=0.29, gap_loss_prob=0.15)
    assert sum(p for _, p in outs) == pytest.approx(1.0)
    # Expected exit price equals fair (optional stopping consistency).
    exit_price_ev = sum((1 + r) * entry * p for r, p in outs)
    assert exit_price_ev == pytest.approx(fair, abs=1e-9)
    # And therefore per-stake EV equals the entry edge.
    ev = sum(r * p for r, p in outs)
    assert ev == pytest.approx((fair - entry) / entry, abs=1e-9)


def test_recommended_fraction_is_conservative_and_scaled():
    f = recommended_fraction(entry_price=0.24, fair=0.30, take_profit_return=0.50,
                             converge_exit_price=0.29, kelly_fraction=0.25)
    assert 0.0 < f <= 0.25 * kelly_binary(0.30, 0.24) + 1e-12


def test_recommended_fraction_zero_without_edge():
    f = recommended_fraction(entry_price=0.30, fair=0.30, take_profit_return=0.50,
                             converge_exit_price=0.29, kelly_fraction=0.25)
    assert f == 0.0
