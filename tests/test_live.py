from datetime import datetime, timedelta, timezone

import pytest

from btc_edge.live.feed import SpotFeed
from btc_edge.live.ledger import Ledger
from btc_edge.live.runner import PaperTrader
from btc_edge.markets.edge import Signal


def make_feed(mid=65_500.0, open_5m=65_400.0, sigma=0.0008):
    f = SpotFeed()
    f.mid = mid
    f.vol.var = sigma ** 2
    now = datetime.now(timezone.utc)
    start = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
    f.window_opens[5] = (start, open_5m)
    f.window_opens[15] = (start, open_5m)
    return f


def test_fair_up_pricer_only(tmp_path):
    trader = PaperTrader(make_feed(), Ledger(tmp_path / "l.jsonl"))
    p, tau = trader.fair_up(5)
    assert 0 < tau <= 5
    assert 0.5 < p < 1.0  # spot above open -> up favored
    # symmetric: spot below open
    trader2 = PaperTrader(make_feed(mid=65_300.0), Ledger(tmp_path / "l2.jsonl"))
    p2, _ = trader2.fair_up(5)
    assert 0.0 < p2 < 0.5
    # cold feed -> None
    cold = SpotFeed()
    assert PaperTrader(cold, Ledger(tmp_path / "l3.jsonl")).fair_up(5) is None


def test_ledger_roundtrip_and_summary(tmp_path):
    led = Ledger(tmp_path / "paper.jsonl")
    sig = Signal(ts_utc="2026-06-11T15:00:00+00:00", venue="polymarket",
                 market_id="mkt1", side="YES", model_p=0.62, entry_price=0.55,
                 fee_est=0.0, edge_net=0.07, kelly=0.15, stake_frac=0.02)
    led.record(sig)
    led.settle("mkt1", outcome_yes=True, settle_price_source="test")
    s = led.summary()
    assert s["signals"] == 1 and s["settled"] == 1
    assert s["hit_rate"] == 1.0
    assert s["avg_pnl_per_$1"] == pytest.approx(0.45)
    assert s["model_brier_on_trades"] == pytest.approx((0.62 - 1) ** 2, abs=1e-6)
    # losing NO trade
    led.record(Signal(ts_utc="t", venue="kalshi", market_id="mkt2", side="NO",
                      model_p=0.3, entry_price=0.6, fee_est=0.01, edge_net=0.1,
                      kelly=0.1, stake_frac=0.01))
    led.settle("mkt2", outcome_yes=True)
    s2 = led.summary()
    assert s2["settled"] == 2 and s2["hit_rate"] == 0.5
