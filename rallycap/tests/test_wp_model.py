from rallycap.types import GameState, Half, Side
from rallycap.wp_model import (
    pregame_win_probability,
    win_probability,
    win_probability_for,
)


def make_state(**kw) -> GameState:
    base = dict(
        game_pk=1, home_team="H", away_team="A",
        inning=1, half=Half.TOP, outs=0,
        on_first=False, on_second=False, on_third=False,
        home_score=0, away_score=0, is_final=False, fetched_at=0.0,
    )
    base.update(kw)
    return GameState(**base)


def test_pregame_home_advantage():
    p = pregame_win_probability()
    assert 0.52 < p < 0.56  # league-average home team wins ~54%


def test_sides_sum_to_one():
    s = make_state(inning=3, half=Half.BOTTOM, home_score=2, away_score=5, outs=1)
    p_home = win_probability_for(s, Side.HOME)
    p_away = win_probability_for(s, Side.AWAY)
    assert abs(p_home + p_away - 1.0) < 1e-9


def test_monotonic_in_score():
    probs = [
        win_probability(make_state(inning=4, half=Half.TOP, home_score=h, away_score=2))
        for h in range(0, 7)
    ]
    assert probs == sorted(probs)
    assert probs[0] < 0.5 < probs[-1]


def test_early_three_run_deficit_in_strategy_band():
    # The canonical screen case: home trailing by 3 in the 2nd should price
    # roughly in the 0.15-0.30 underdog band, not at a coin flip or at zero.
    s = make_state(inning=2, half=Half.TOP, outs=1, on_first=True,
                   home_score=0, away_score=3)
    p = win_probability(s)
    assert 0.10 < p < 0.30


def test_late_big_lead_near_certain():
    s = make_state(inning=8, half=Half.BOTTOM, outs=2, home_score=8, away_score=1)
    assert win_probability(s) > 0.985


def test_bottom_nine_tie_favors_home():
    s = make_state(inning=9, half=Half.BOTTOM, home_score=4, away_score=4)
    p = win_probability(s)
    assert 0.58 < p < 0.75  # bats last with a chance to walk it off


def test_final_states_are_absorbing():
    assert win_probability(make_state(home_score=3, away_score=5, is_final=True, inning=9)) == 0.0
    assert win_probability(make_state(home_score=5, away_score=3, is_final=True, inning=9)) == 1.0


def test_ratings_shift_probability():
    s = make_state()
    strong_home = win_probability(s, home_rating_runs=0.5)
    weak_home = win_probability(s, home_rating_runs=-0.5)
    assert strong_home > pregame_win_probability() > weak_home


def test_runner_on_third_helps_batting_team():
    base = make_state(inning=5, half=Half.BOTTOM, outs=1)
    runner = make_state(inning=5, half=Half.BOTTOM, outs=1, on_third=True)
    assert win_probability(runner) > win_probability(base)
