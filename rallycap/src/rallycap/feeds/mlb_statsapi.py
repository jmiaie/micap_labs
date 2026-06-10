"""MLB Stats API adapter (statsapi.mlb.com — free, official, no key).

Provides the schedule (to find today's gamePks/teams) and live game state
(inning, half, outs, bases, score) parsed into our `GameState` snapshot.

Score-change detection lives here: the feed remembers the last score per game
and stamps `last_score_change_at`, which drives the post-event freeze window.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

from ..types import GameState, Half

log = logging.getLogger(__name__)

STATS_BASE = "https://statsapi.mlb.com/api"


def _client():
    import httpx  # lazy

    return httpx.Client(timeout=10.0, headers={"User-Agent": "rallycap/0.1"})


class MlbStatsFeed:
    def __init__(self) -> None:
        self._http = None
        # game_pk -> (last (home, away) score, when it last changed)
        self._score_memory: Dict[int, Tuple[Tuple[int, int], float]] = {}

    @property
    def http(self):
        if self._http is None:
            self._http = _client()
        return self._http

    def schedule(self, date_yyyy_mm_dd: str) -> List[dict]:
        """Games for a date: [{game_pk, home_name, away_name, status}, ...]."""
        resp = self.http.get(
            f"{STATS_BASE}/v1/schedule",
            params={"sportId": 1, "date": date_yyyy_mm_dd},
        )
        resp.raise_for_status()
        games = []
        for day in resp.json().get("dates", []):
            for g in day.get("games", []):
                games.append(
                    {
                        "game_pk": g["gamePk"],
                        "home_name": g["teams"]["home"]["team"]["name"],
                        "away_name": g["teams"]["away"]["team"]["name"],
                        "status": g.get("status", {}).get("abstractGameState", ""),
                    }
                )
        return games

    def live_state(self, game_pk: int) -> Optional[GameState]:
        """Current state from the v1.1 live feed; None on parse trouble."""
        resp = self.http.get(f"{STATS_BASE}/v1.1/game/{game_pk}/feed/live")
        resp.raise_for_status()
        data = resp.json()
        try:
            return self.parse_live_feed(game_pk, data, now=time.time())
        except (KeyError, TypeError) as exc:
            log.warning("gamePk %s: live feed parse failed: %s", game_pk, exc)
            return None

    def parse_live_feed(self, game_pk: int, data: dict, now: float) -> GameState:
        """Pure parser, separated for testability with canned fixtures."""
        game_data = data["gameData"]
        live = data["liveData"]
        linescore = live["linescore"]

        status = game_data.get("status", {}).get("abstractGameState", "")
        is_final = status == "Final"

        offense = linescore.get("offense", {})
        half_str = (linescore.get("inningHalf") or "Top").lower()
        home_score = linescore.get("teams", {}).get("home", {}).get("runs", 0) or 0
        away_score = linescore.get("teams", {}).get("away", {}).get("runs", 0) or 0

        last_change = self._note_score(game_pk, (home_score, away_score), now)

        return GameState(
            game_pk=game_pk,
            home_team=game_data["teams"]["home"]["name"],
            away_team=game_data["teams"]["away"]["name"],
            inning=int(linescore.get("currentInning", 1) or 1),
            half=Half.BOTTOM if half_str.startswith("bot") else Half.TOP,
            outs=min(int(linescore.get("outs", 0) or 0), 2),
            on_first="first" in offense,
            on_second="second" in offense,
            on_third="third" in offense,
            home_score=home_score,
            away_score=away_score,
            is_final=is_final,
            fetched_at=now,
            last_score_change_at=last_change,
        )

    def _note_score(self, game_pk: int, score: Tuple[int, int], now: float) -> float:
        """Timestamp of the last observed score change. First sight of a game
        counts as a change: we may have joined mid-rally, so start frozen."""
        prev = self._score_memory.get(game_pk)
        if prev is None or prev[0] != score:
            self._score_memory[game_pk] = (score, now)
            return now
        return prev[1]
