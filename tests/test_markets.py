"""Market client parsing tests with canned HTTP fixtures (no network)."""

import json

from btc_edge.markets.kalshi import KalshiClient
from btc_edge.markets.polymarket import PolymarketClient


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResp(self.payloads.pop(0))


GAMMA_MARKETS = [
    {   # matching 5-min up/down market
        "id": 101, "question": "Bitcoin Up or Down - June 11, 3:05 PM ET",
        "slug": "btc-updown-5m", "clobTokenIds": json.dumps(["tokY", "tokN"]),
        "startDate": "2026-06-11T19:05:00Z", "endDate": "2026-06-11T19:10:00Z",
    },
    {   # non-BTC market filtered out
        "id": 102, "question": "Ethereum Up or Down - June 11, 3:05 PM ET",
        "slug": "eth", "clobTokenIds": json.dumps(["a", "b"]),
        "startDate": "2026-06-11T19:05:00Z", "endDate": "2026-06-11T19:10:00Z",
    },
    {   # BTC but hourly (filtered out by window_min=5)
        "id": 103, "question": "Bitcoin Up or Down - 3 PM ET hourly",
        "slug": "btc-hourly", "clobTokenIds": json.dumps(["c", "d"]),
        "startDate": "2026-06-11T19:00:00Z", "endDate": "2026-06-11T20:00:00Z",
    },
]

CLOB_BOOK = {
    "bids": [{"price": "0.47", "size": "100"}, {"price": "0.45", "size": "300"}],
    "asks": [{"price": "0.53", "size": "80"}, {"price": "0.55", "size": "200"}],
}


def test_polymarket_discovery_and_quote():
    s = FakeSession([GAMMA_MARKETS, CLOB_BOOK])
    c = PolymarketClient(session=s)
    found = c.find_btc_updown(window_min=5)
    assert len(found) == 1
    m = found[0]
    assert m.token_yes == "tokY"
    assert m.window_minutes == 5.0

    quote = c.quote(m)
    assert quote.yes_bid == 0.47 and quote.yes_ask == 0.53
    assert quote.venue == "polymarket"
    assert abs(quote.mid - 0.5) < 1e-9


KALSHI_MARKETS = {
    "markets": [
        {"ticker": "KXBTC-26JUN1115-T62250", "title": "BTC above 62,250 at 3pm?",
         "floor_strike": 62250, "close_time": "2026-06-11T19:00:00Z",
         "yes_bid": 41, "yes_ask": 44, "volume": 1234},
    ]
}


def test_kalshi_markets_and_quote():
    c = KalshiClient(session=FakeSession([KALSHI_MARKETS]))
    ms = c.open_markets("KXBTC")
    assert len(ms) == 1
    m = ms[0]
    assert m.strike == 62250
    q = c.quote(m)
    assert q.yes_bid == 0.41 and q.yes_ask == 0.44
    assert q.meta["strike"] == 62250
