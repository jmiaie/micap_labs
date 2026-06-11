"""Canonical MLB team table and matching.

Market discovery has to join three naming worlds: MLB Stats API full names
("Boston Red Sox"), Polymarket outcome labels (usually nicknames, "Red Sox"),
and event titles ("Yankees vs. Red Sox"). This module is the single source of
truth for that join. Matching is deliberately conservative: ambiguous labels
return None rather than a guess — a wrong join here means trading the wrong
game.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional, Set, Tuple


@dataclass(frozen=True)
class Team:
    abbr: str            # canonical key used across rallycap
    statsapi_name: str   # full name as MLB Stats API spells it
    nickname: str        # the part Polymarket outcomes usually carry
    aliases: Tuple[str, ...] = field(default=())


TEAMS: Tuple[Team, ...] = (
    Team("AZ", "Arizona Diamondbacks", "Diamondbacks", ("ARI", "D-backs", "Dbacks")),
    Team("ATL", "Atlanta Braves", "Braves"),
    Team("BAL", "Baltimore Orioles", "Orioles", ("O's",)),
    Team("BOS", "Boston Red Sox", "Red Sox"),
    Team("CHC", "Chicago Cubs", "Cubs"),
    Team("CWS", "Chicago White Sox", "White Sox", ("CHW",)),
    Team("CIN", "Cincinnati Reds", "Reds"),
    Team("CLE", "Cleveland Guardians", "Guardians"),
    Team("COL", "Colorado Rockies", "Rockies"),
    Team("DET", "Detroit Tigers", "Tigers"),
    Team("HOU", "Houston Astros", "Astros"),
    Team("KC", "Kansas City Royals", "Royals", ("KCR",)),
    Team("LAA", "Los Angeles Angels", "Angels"),
    Team("LAD", "Los Angeles Dodgers", "Dodgers"),
    Team("MIA", "Miami Marlins", "Marlins"),
    Team("MIL", "Milwaukee Brewers", "Brewers"),
    Team("MIN", "Minnesota Twins", "Twins"),
    Team("NYM", "New York Mets", "Mets"),
    Team("NYY", "New York Yankees", "Yankees"),
    Team("ATH", "Athletics", "Athletics", ("A's", "OAK", "Oakland Athletics")),
    Team("PHI", "Philadelphia Phillies", "Phillies"),
    Team("PIT", "Pittsburgh Pirates", "Pirates"),
    Team("SD", "San Diego Padres", "Padres", ("SDP",)),
    Team("SF", "San Francisco Giants", "Giants", ("SFG",)),
    Team("SEA", "Seattle Mariners", "Mariners"),
    Team("STL", "St. Louis Cardinals", "Cardinals", ("Saint Louis Cardinals",)),
    Team("TB", "Tampa Bay Rays", "Rays", ("TBR",)),
    Team("TEX", "Texas Rangers", "Rangers"),
    Team("TOR", "Toronto Blue Jays", "Blue Jays", ("Jays",)),
    Team("WSH", "Washington Nationals", "Nationals", ("Nats", "WSN")),
)


def _norm(s: str) -> str:
    """lowercase, strip punctuation, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", s.lower())).strip()


def _build_index() -> Dict[str, str]:
    index: Dict[str, str] = {}
    for t in TEAMS:
        for key in (t.abbr, t.statsapi_name, t.nickname, *t.aliases):
            index[_norm(key)] = t.abbr
    return index


_INDEX: Dict[str, str] = _build_index()
# Multi-word nicknames must be scanned before single words ("Red Sox" before
# any token scan — a bare "Sox" is ambiguous and matches nothing).
_MULTIWORD_NICKS: Tuple[Tuple[str, str], ...] = tuple(
    (_norm(t.nickname), t.abbr) for t in TEAMS if " " in t.nickname
)
_SINGLEWORD_NICKS: Dict[str, str] = {
    _norm(t.nickname): t.abbr for t in TEAMS if " " not in t.nickname
}


def match_team(label: str) -> Optional[str]:
    """Canonical abbreviation for a team label, or None.

    Exact (normalized) match against full names, nicknames, abbreviations and
    aliases only — no fuzzy guessing for a whole-label match.
    """
    return _INDEX.get(_norm(label))


def find_teams_in_text(text: str) -> Set[str]:
    """All teams whose nickname appears (word-bounded) in free text, e.g. a
    Gamma event title like "Yankees vs. Red Sox"."""
    padded = f" {_norm(text)} "
    found: Set[str] = set()
    for nick, abbr in _MULTIWORD_NICKS:
        if f" {nick} " in padded:
            found.add(abbr)
    tokens = set(padded.split())
    for nick, abbr in _SINGLEWORD_NICKS.items():
        if nick in tokens:
            found.add(abbr)
    return found


def team_pair(home_label: str, away_label: str) -> Optional[FrozenSet[str]]:
    """Frozenset of the two canonical abbreviations, or None if either label
    fails to match or both resolve to the same team."""
    h, a = match_team(home_label), match_team(away_label)
    if h is None or a is None or h == a:
        return None
    return frozenset((h, a))
