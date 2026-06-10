"""Parser tests against realistic API payload fixtures.

The fixtures mirror the documented/observed shapes of the MLB Stats API live
feed, the Gamma events API, and the CLOB book endpoint. If a venue changes
its schema, these tests are where the change gets encoded first.
"""

import json

from rallycap.feeds.mlb_statsapi import MlbStatsFeed
from rallycap.feeds.polymarket import ClobMarketData, GammaClient
from rallycap.feeds.sharp_odds import american_to_implied, devig_american, devig_two_way
from rallycap.types import Half

# --------------------------------------------------------------- MLB statsapi

def live_feed_fixture(*, home_runs=2, away_runs=5, inning=3, half="Bottom",
                      outs=1, final=False, on_first=True, on_third=True) -> dict:
    offense = {"batter": {"id": 1}, "pitcher": {"id": 2}}
    if on_first:
        offense["first"] = {"id": 660271}
    if on_third:
        offense["third"] = {"id": 545361}
    return {
        "gameData": {
            "status": {"abstractGameState": "Final" if final else "Live"},
            "teams": {
                "home": {"name": "Boston Red Sox"},
                "away": {"name": "New York Yankees"},
            },
        },
        "liveData": {
            "linescore": {
                "currentInning": inning,
                "inningHalf": half,
                "outs": outs,
                "offense": offense,
                "teams": {
                    "home": {"runs": home_runs},
                    "away": {"runs": away_runs},
                },
            }
        },
    }


def test_parse_live_feed_basic():
    feed = MlbStatsFeed()
    state = feed.parse_live_feed(715723, live_feed_fixture(), now=1000.0)
    assert state.game_pk == 715723
    assert state.home_team == "Boston Red Sox"
    assert state.away_team == "New York Yankees"
    assert state.inning == 3 and state.half is Half.BOTTOM and state.outs == 1
    assert state.on_first and state.on_third and not state.on_second
    assert state.home_score == 2 and state.away_score == 5
    assert not state.is_final
    assert state.fetched_at == 1000.0


def test_parse_live_feed_final():
    feed = MlbStatsFeed()
    state = feed.parse_live_feed(1, live_feed_fixture(final=True), now=1000.0)
    assert state.is_final


def test_score_change_memory_drives_freeze_window():
    feed = MlbStatsFeed()
    s1 = feed.parse_live_feed(1, live_feed_fixture(away_runs=5), now=1000.0)
    # First sight counts as a change: we may have joined mid-rally.
    assert s1.last_score_change_at == 1000.0
    s2 = feed.parse_live_feed(1, live_feed_fixture(away_runs=5), now=1010.0)
    assert s2.last_score_change_at == 1000.0      # calm: stamp unchanged
    s3 = feed.parse_live_feed(1, live_feed_fixture(away_runs=7), now=1020.0)
    assert s3.last_score_change_at == 1020.0      # scoring play: new stamp
    assert s3.seconds_since_score_change(now=1030.0) == 10.0


def test_outs_clamped_to_two():
    # Between half-innings statsapi can report 3 outs; the model wants 0-2.
    feed = MlbStatsFeed()
    state = feed.parse_live_feed(1, live_feed_fixture(outs=3), now=0.0)
    assert state.outs == 2


# ------------------------------------------------------------------ Gamma API

def gamma_event_fixture(outcomes=("Yankees", "Red Sox"),
                        tokens=("111111", "222222")) -> dict:
    return {
        "title": f"{outcomes[0]} vs. {outcomes[1]}",
        "slug": "mlb-nyy-bos-2026-06-10",
        "markets": [
            {
                "conditionId": "0xabc123",
                "clobTokenIds": json.dumps(list(tokens)),
                "outcomes": json.dumps(list(outcomes)),
            }
        ],
    }


def test_parse_game_market_maps_tokens_by_team_identity():
    event = gamma_event_fixture(outcomes=("Yankees", "Red Sox"))
    market = GammaClient.parse_game_market(
        event, 715723, home_label="Boston Red Sox", away_label="New York Yankees"
    )
    assert market is not None
    assert market.game_pk == 715723
    # Red Sox are home: home token must be the one paired with "Red Sox",
    # regardless of outcome array order.
    assert market.home_token_id == "222222"
    assert market.away_token_id == "111111"
    assert market.home_team == "Red Sox"


def test_parse_game_market_order_independent():
    flipped = gamma_event_fixture(outcomes=("Red Sox", "Yankees"),
                                  tokens=("333333", "444444"))
    market = GammaClient.parse_game_market(
        flipped, 1, home_label="Boston Red Sox", away_label="New York Yankees"
    )
    assert market is not None
    assert market.home_token_id == "333333"


def test_parse_game_market_rejects_wrong_game():
    event = gamma_event_fixture(outcomes=("Dodgers", "Padres"))
    market = GammaClient.parse_game_market(
        event, 1, home_label="Boston Red Sox", away_label="New York Yankees"
    )
    assert market is None


def test_parse_game_market_rejects_malformed_event():
    assert GammaClient.parse_game_market({}, 1, "Boston Red Sox", "New York Yankees") is None
    bad = {"markets": [{"clobTokenIds": "not json", "outcomes": "[]"}]}
    assert GammaClient.parse_game_market(bad, 1, "Boston Red Sox", "New York Yankees") is None


# ------------------------------------------------------------------- CLOB book

def test_parse_book_picks_best_levels_regardless_of_order():
    payload = {
        "market": "0xabc",
        "asset_id": "111111",
        "bids": [
            {"price": "0.15", "size": "1200"},
            {"price": "0.18", "size": "350"},   # best bid, not last
            {"price": "0.17", "size": "90"},
        ],
        "asks": [
            {"price": "0.22", "size": "60"},
            {"price": "0.20", "size": "410"},   # best ask, not first
        ],
    }
    book = ClobMarketData.parse_book("111111", payload, now=5.0)
    assert book.bid == 0.18 and book.bid_size == 350.0
    assert book.ask == 0.20 and book.ask_size == 410.0
    assert abs(book.spread - 0.02) < 1e-12
    assert book.fetched_at == 5.0


def test_parse_book_handles_empty_side():
    book = ClobMarketData.parse_book("1", {"bids": [], "asks": None}, now=0.0)
    assert book.bid is None and book.ask is None
    assert book.spread == float("inf")


# ------------------------------------------------------------------- de-vig

def test_american_to_implied():
    assert abs(american_to_implied(-150) - 0.60) < 1e-9
    assert abs(american_to_implied(+150) - 0.40) < 1e-9


def test_devig_two_way_normalizes():
    p_home, p_away = devig_two_way(0.55, 0.50)  # 105% book
    assert abs(p_home + p_away - 1.0) < 1e-12
    assert p_home > p_away


def test_devig_american_round_trip():
    # -300 / +240 live line: de-vigged probs sum to 1, favorite ~73-75%.
    p_home, p_away = devig_american(-300, 240)
    assert abs(p_home + p_away - 1.0) < 1e-12
    assert 0.70 < p_home < 0.78
