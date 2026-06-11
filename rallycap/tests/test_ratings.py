import pytest

from rallycap.ratings import implied_rating_diff, ratings_from_pregame_prob
from rallycap.wp_model import pregame_win_probability


def test_model_default_prob_implies_zero_gap():
    p_default = pregame_win_probability()
    assert abs(implied_rating_diff(p_default)) < 0.02


def test_round_trip_reproduces_market_prob():
    for p in (0.40, 0.50, 0.534, 0.62, 0.70):
        r = ratings_from_pregame_prob(p)
        back = pregame_win_probability(r.home_rating_runs, r.away_rating_runs)
        assert back == pytest.approx(p, abs=1e-3)


def test_monotonic_in_probability():
    diffs = [implied_rating_diff(p) for p in (0.35, 0.45, 0.55, 0.65, 0.75)]
    assert diffs == sorted(diffs)


def test_symmetric_split():
    r = ratings_from_pregame_prob(0.65)
    assert r.home_rating_runs == pytest.approx(-r.away_rating_runs)
    assert r.home_rating_runs > 0


def test_extreme_probs_clamp_instead_of_blowing_up():
    assert implied_rating_diff(0.99) <= 5.0
    assert implied_rating_diff(0.01) >= -5.0
