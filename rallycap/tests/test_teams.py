from rallycap.feeds.teams import TEAMS, find_teams_in_text, match_team, team_pair


def test_all_statsapi_names_resolve():
    for t in TEAMS:
        assert match_team(t.statsapi_name) == t.abbr


def test_all_nicknames_resolve():
    for t in TEAMS:
        assert match_team(t.nickname) == t.abbr


def test_no_index_collisions_across_teams():
    # Every key form of every team must resolve to that team and no other.
    for t in TEAMS:
        for key in (t.abbr, t.statsapi_name, t.nickname, *t.aliases):
            assert match_team(key) == t.abbr, key


def test_label_variants():
    assert match_team("yankees") == "NYY"
    assert match_team("NEW YORK YANKEES") == "NYY"
    assert match_team("St. Louis Cardinals") == "STL"
    assert match_team("D-backs") == "AZ"
    assert match_team("A's") == "ATH"
    assert match_team("Oakland Athletics") == "ATH"  # legacy labels still seen


def test_ambiguous_or_unknown_returns_none():
    assert match_team("Sox") is None          # could be either Sox
    assert match_team("New York") is None     # could be either NY team
    assert match_team("Springfield Isotopes") is None


def test_find_teams_in_title():
    assert find_teams_in_text("Yankees vs. Red Sox") == {"NYY", "BOS"}
    assert find_teams_in_text("White Sox at Guardians (game)") == {"CWS", "CLE"}
    assert find_teams_in_text("MLB: Will the Dodgers win the World Series?") == {"LAD"}
    assert find_teams_in_text("no baseball here") == set()


def test_team_pair():
    assert team_pair("Boston Red Sox", "New York Yankees") == frozenset({"BOS", "NYY"})
    assert team_pair("Boston Red Sox", "Red Sox") is None      # same team twice
    assert team_pair("Boston Red Sox", "Springfield") is None  # unknown side
