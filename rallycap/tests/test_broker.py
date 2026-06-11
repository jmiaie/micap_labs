import pytest

from rallycap.execution.broker import PaperBroker
from rallycap.types import BookTop, Side


def book(bid=0.17, ask=0.20, bid_size=500.0, ask_size=500.0, token="T") -> BookTop:
    return BookTop(token_id=token, bid=bid, bid_size=bid_size,
                   ask=ask, ask_size=ask_size, fetched_at=0.0)


def test_crossing_buy_fills_at_ask_capped_by_depth():
    b = PaperBroker(cash=1000.0)
    fill = b.buy("T", Side.HOME, limit_price=0.20, size_shares=800.0,
                 book=book(ask_size=300.0), cross=True)
    assert fill is not None
    assert fill.price == 0.20 and fill.size_shares == 300.0
    assert b.cash == pytest.approx(1000.0 - 0.20 * 300.0)


def test_passive_buy_rests_and_reserves_cash():
    b = PaperBroker(cash=1000.0)
    fill = b.buy("T", Side.HOME, limit_price=0.18, size_shares=100.0,
                 book=book(), cross=False)
    assert fill is None
    order = b.resting_order("T")
    assert order is not None and order.limit_price == 0.18
    assert b.cash == pytest.approx(1000.0 - 18.0)
    assert b.resting_count() == 1
    assert b.resting_cost() == pytest.approx(18.0)


def test_resting_fill_requires_strict_trade_through():
    b = PaperBroker(cash=1000.0)
    b.buy("T", Side.HOME, 0.18, 100.0, book=book(), cross=False)
    # Ask AT our limit: we are in queue at the level, assume no fill.
    assert b.try_fill_resting("T", book(bid=0.16, ask=0.18), now=1.0) is None
    assert b.resting_order("T") is not None
    # Ask BELOW our limit: traded through, fill at OUR price, full size.
    fill = b.try_fill_resting("T", book(bid=0.15, ask=0.17), now=2.0)
    assert fill is not None
    assert fill.price == 0.18 and fill.size_shares == 100.0
    assert b.resting_order("T") is None
    # Cash was reserved at placement; the fill moves no additional cash.
    assert b.cash == pytest.approx(1000.0 - 18.0)


def test_cancel_refunds_reservation():
    b = PaperBroker(cash=1000.0)
    b.buy("T", Side.HOME, 0.18, 100.0, book=book(), cross=False)
    b.cancel_all("T")
    assert b.resting_order("T") is None
    assert b.cash == pytest.approx(1000.0)


def test_replacing_a_resting_order_refunds_the_old_one():
    b = PaperBroker(cash=100.0)
    b.buy("T", Side.HOME, 0.18, 100.0, book=book(), cross=False)   # reserves 18
    b.buy("T", Side.HOME, 0.19, 200.0, book=book(), cross=False)   # reserves 38
    order = b.resting_order("T")
    assert order is not None and order.limit_price == 0.19 and order.size_shares == 200.0
    assert b.cash == pytest.approx(100.0 - 38.0)


def test_passive_buy_that_would_cross_fills_immediately():
    b = PaperBroker(cash=1000.0)
    fill = b.buy("T", Side.HOME, limit_price=0.21, size_shares=100.0,
                 book=book(ask=0.20), cross=False)
    assert fill is not None and fill.price == 0.20
    assert b.resting_order("T") is None


def test_resting_rejected_when_unaffordable():
    b = PaperBroker(cash=10.0)
    b.buy("T", Side.HOME, 0.18, 100.0, book=book(), cross=False)  # needs 18
    assert b.resting_order("T") is None
    assert b.cash == pytest.approx(10.0)
