import math
import random

import pytest

from rallycap.backtest.calibrate import (
    GameLineScore,
    brier_score,
    calibrate,
    load_line_scores_jsonl,
)
from rallycap.backtest.engine import RUNS_PMF

# The PMF's true moments, computed directly: the calibrator must recover
# these from generated games (not the hand-set wp_model constants — that gap
# is exactly what calibration exists to expose).
PMF_MEAN = sum(r * p for r, p in RUNS_PMF)
PMF_STD = math.sqrt(sum(r * r * p for r, p in RUNS_PMF) - PMF_MEAN**2)


def sample_runs(rng: random.Random) -> int:
    x, acc = rng.random(), 0.0
    for runs, prob in RUNS_PMF:
        acc += prob
        if x <= acc:
            return runs
    return 0


def generate_game(rng: random.Random) -> GameLineScore:
    """Symmetric teams, full half-inning scoring (no walk-off truncation —
    fine for moment recovery; see calibrate() docstring for real-data
    caveats), extras until decided."""
    home, away = [], []
    inning = 0
    while True:
        inning += 1
        away.append(sample_runs(rng))
        if inning == 9 and sum(home) > sum(away):
            break  # bottom 9 not needed: home already leads
        home.append(sample_runs(rng))
        if inning >= 9 and sum(home) != sum(away):
            break
    return GameLineScore(home=tuple(home), away=tuple(away))


def test_recovers_pmf_moments_from_generated_games():
    rng = random.Random(123)
    games = [generate_game(rng) for _ in range(4000)]
    report = calibrate(games)
    assert report.n_games == 4000
    assert report.runs_per_half_inning == pytest.approx(PMF_MEAN, abs=0.02)
    assert report.sigma_per_half_inning == pytest.approx(PMF_STD, abs=0.03)
    # Symmetric teams: no home field advantage to find.
    assert report.home_field_runs_per_game == pytest.approx(0.0, abs=0.20)
    # Symmetric extras: tie-game home win rate near a coin flip.
    assert report.n_tie_games > 100
    assert 0.40 < report.home_tie_win_prob < 0.60


def test_calibration_exposes_simulator_model_mismatch():
    # The synthetic simulator's run process is over-dispersed relative to the
    # hand-set SIGMA_PER_HALF_INNING (0.74). Calibration must surface that
    # gap rather than echo the constant back.
    from rallycap import wp_model

    assert PMF_STD > wp_model.SIGMA_PER_HALF_INNING + 0.15


def test_shortened_games_are_excluded():
    ok = GameLineScore(home=(0,) * 9, away=(1,) + (0,) * 8)
    rain = GameLineScore(home=(0,) * 5, away=(1,) + (0,) * 4)
    report = calibrate([ok, rain])
    assert report.n_games == 1


def test_walkoff_skipped_bottom_ninth_is_usable():
    g = GameLineScore(home=(1, 0, 0, 0, 0, 0, 0, 1), away=(0,) * 9)  # home led after top 9
    report = calibrate([g])
    assert report.n_games == 1
    assert report.n_tie_games == 0


def test_calibrate_raises_on_no_usable_games():
    with pytest.raises(ValueError):
        calibrate([GameLineScore(home=(1,), away=(0,))])


def test_jsonl_round_trip(tmp_path):
    p = tmp_path / "games.jsonl"
    p.write_text('{"home": [0,0,1,0,0,0,2,0,0], "away": [2,0,0,0,0,0,0,0,1]}\n\n'
                 '{"home": [0,0,0,0,0,0,0,0], "away": [0,1,0,0,0,0,0,0,0]}\n')
    games = load_line_scores_jsonl(str(p))
    assert len(games) == 2
    assert games[0].home_total == 3 and games[0].away_total == 3
    assert len(games[1].home) == 8  # walk-off-skipped bottom 9 preserved


def test_brier_score():
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)
    # Sharper correct forecasts strictly beat hedged ones.
    assert brier_score([0.9, 0.1], [1, 0]) < brier_score([0.6, 0.4], [1, 0])
    with pytest.raises(ValueError):
        brier_score([0.5], [1, 0])
