"""Calibration tooling for the WP model constants (PRD M1).

The win-probability model carries four hand-set constants
(`LEAGUE_RUNS_PER_INNING`, `SIGMA_PER_HALF_INNING`, `HOME_FIELD_RUNS`,
`HOME_TIE_WIN_PROB`). This module fits them from historical line scores and
reports fitted-vs-current so the constants can be updated deliberately.

Input: per-game line scores — runs by half-inning. JSONL schema, one game per
row, innings in order, home list shorter when the bottom 9th wasn't played:

    {"home": [0,0,1,0,0,0,2,0,0], "away": [2,0,0,0,0,0,0,0,1]}

Sources: Retrosheet game logs (line score fields) or MLB Stats API boxscores.

Method notes:
  - Per-half-inning mean/variance use innings 1-8 only: the 9th is truncated
    (walk-offs, skipped bottom halves) and would bias both moments down.
  - HOME_TIE_WIN_PROB is the home win rate among games tied after 9.
  - `brier_score` supports the M1 gate: the model must match/beat the
    market's Brier on a holdout before model-edge trading is licensed.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .. import wp_model


@dataclass(frozen=True)
class GameLineScore:
    home: Tuple[int, ...]   # runs per inning batted, in order
    away: Tuple[int, ...]

    @property
    def home_total(self) -> int:
        return sum(self.home)

    @property
    def away_total(self) -> int:
        return sum(self.away)


@dataclass(frozen=True)
class CalibrationReport:
    n_games: int
    runs_per_half_inning: float
    sigma_per_half_inning: float
    home_field_runs_per_game: float
    home_tie_win_prob: Optional[float]
    n_tie_games: int

    def summary(self) -> str:
        tie = (f"{self.home_tie_win_prob:.3f} (n={self.n_tie_games})"
               if self.home_tie_win_prob is not None else f"n/a (n={self.n_tie_games})")
        rows = [
            ("runs/half-inning", self.runs_per_half_inning, wp_model.LEAGUE_RUNS_PER_INNING),
            ("sigma/half-inning", self.sigma_per_half_inning, wp_model.SIGMA_PER_HALF_INNING),
            ("home field (runs/game)", self.home_field_runs_per_game, wp_model.HOME_FIELD_RUNS),
        ]
        lines = [f"calibration over {self.n_games} games (innings 1-8 moments):"]
        for name, fitted, current in rows:
            lines.append(f"  {name:24s} fitted {fitted:7.3f}   current {current:7.3f}   "
                         f"delta {fitted - current:+.3f}")
        lines.append(f"  {'home tie win prob':24s} fitted {tie:>7s}   "
                     f"current {wp_model.HOME_TIE_WIN_PROB:7.3f}")
        lines.append("apply by updating the constants in wp_model.py, then re-run the "
                     "Brier gate (PRD M1).")
        return "\n".join(lines)


def calibrate(games: Iterable[GameLineScore]) -> CalibrationReport:
    home_samples: List[int] = []
    away_samples: List[int] = []
    ties = 0
    tie_home_wins = 0
    n = 0
    for g in games:
        if len(g.away) < 9 or len(g.home) < 8:
            continue  # shortened game: not usable for untruncated moments
        n += 1
        home_samples.extend(g.home[:8])
        away_samples.extend(g.away[:8])
        if sum(g.home[:9]) == sum(g.away[:9]) and len(g.home) >= 9:
            ties += 1
            if g.home_total > g.away_total:
                tie_home_wins += 1
    if n == 0:
        raise ValueError("no usable games (need >= 8 home and >= 9 away innings)")

    both = home_samples + away_samples
    mean = sum(both) / len(both)
    var = sum((x - mean) ** 2 for x in both) / len(both)
    mean_h = sum(home_samples) / len(home_samples)
    mean_a = sum(away_samples) / len(away_samples)

    return CalibrationReport(
        n_games=n,
        runs_per_half_inning=mean,
        sigma_per_half_inning=math.sqrt(var),
        home_field_runs_per_game=(mean_h - mean_a) * 9.0,
        home_tie_win_prob=(tie_home_wins / ties) if ties else None,
        n_tie_games=ties,
    )


def load_line_scores_jsonl(path: str) -> List[GameLineScore]:
    games: List[GameLineScore] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        games.append(GameLineScore(home=tuple(row["home"]), away=tuple(row["away"])))
    return games


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Mean squared error of probability forecasts against 0/1 outcomes.
    Lower is better; the market's own price series is the benchmark."""
    if len(probs) != len(outcomes) or not probs:
        raise ValueError("probs and outcomes must be equal-length and non-empty")
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)
