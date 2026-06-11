from datetime import datetime, timezone

import pytest

from btc_edge.config import KALSHI_FEES, POLYMARKET_FEES, SizingConfig
from btc_edge.markets.edge import EdgeEngine, Quote


def q(bid=0.48, ask=0.52):
    return Quote(venue="polymarket", market_id="m1", yes_bid=bid, yes_ask=ask,
                 expiry_utc=datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_no_trade_when_no_edge():
    eng = EdgeEngine(POLYMARKET_FEES, SizingConfig(min_edge=0.03))
    assert eng.evaluate(0.53, q()) is None          # 0.53 - 0.52 = 0.01 < 0.03
    assert eng.evaluate(0.5, q()) is None
    assert eng.evaluate(1.5, q()) is None           # garbage p rejected


def test_yes_side_edge_and_kelly():
    eng = EdgeEngine(POLYMARKET_FEES, SizingConfig(min_edge=0.03, kelly_fraction=0.25,
                                                   max_stake_frac=1.0))
    sig = eng.evaluate(0.60, q())
    assert sig is not None and sig.side == "YES"
    assert sig.entry_price == 0.52
    assert sig.edge_net == pytest.approx(0.08, abs=1e-9)
    # kelly = (p - price)/(1 - price) = 0.08/0.48; quarter kelly applied
    assert sig.kelly == pytest.approx(0.08 / 0.48, abs=1e-3)
    assert sig.stake_frac == pytest.approx(0.25 * 0.08 / 0.48, abs=1e-3)


def test_no_side_symmetric():
    eng = EdgeEngine(POLYMARKET_FEES, SizingConfig(min_edge=0.03))
    sig = eng.evaluate(0.40, q())
    assert sig is not None and sig.side == "NO"
    assert sig.entry_price == pytest.approx(0.52)   # 1 - bid
    assert sig.edge_net == pytest.approx(0.60 - 0.52, abs=1e-9)


def test_kalshi_fees_raise_bar():
    pm = EdgeEngine(POLYMARKET_FEES, SizingConfig(min_edge=0.03))
    ka = EdgeEngine(KALSHI_FEES, SizingConfig(min_edge=0.03))
    p = 0.565
    assert pm.evaluate(p, q()) is not None
    # kalshi taker fee at 0.52 = 0.07*0.52*0.48 = 0.0175 -> edge 0.045-0.0175 < 0.03
    assert ka.evaluate(p, q()) is None


def test_stake_cap():
    eng = EdgeEngine(POLYMARKET_FEES, SizingConfig(min_edge=0.01, kelly_fraction=1.0,
                                                   max_stake_frac=0.02))
    sig = eng.evaluate(0.95, q())
    assert sig.stake_frac == 0.02
