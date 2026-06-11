import numpy as np
import pandas as pd

from btc_edge.config import BacktestConfig, KALSHI_FEES, POLYMARKET_FEES
from btc_edge.backtest import walk_forward, metrics
from btc_edge.data import synthetic_bars, validate_bars
from btc_edge.features import build_features, make_labels
from btc_edge.features.labels import aligned_xy
from btc_edge.models import DirectionModel


def _xy(n_days=12, momentum=0.3, horizon=5):
    # momentum=0.25 is deliberately unrealistic: these tests verify the
    # pipeline can RECOVER a known signal, not that BTC has one this size.
    bars = validate_bars(synthetic_bars(n_days=n_days, seed=11, momentum=momentum))
    f = build_features(bars)
    lab = make_labels(bars, horizons_min=(horizon,))
    return aligned_xy(f, lab, horizon)


def test_walkforward_no_leakage_and_signal_recovery():
    X, y, fwd = _xy()
    cfg = BacktestConfig(train_days=5, test_days=1, calib_days=1,
                         purge_minutes=30, min_train_rows=2000)
    res = walk_forward(X, y, fwd, 5, lambda: DirectionModel("logistic"),
                       cfg, verbose=False)
    pred = res.predictions
    # every test timestamp strictly after its fold's train+purge boundary
    for _, row in res.fold_log.iterrows():
        fold_pred = pred[pred["fold"] == row["fold"]]
        assert fold_pred.index.min() >= row["test_start"]
    # no duplicated timestamps across folds
    assert pred.index.is_unique
    # synthetic AR(1) signal exists -> directional accuracy must be
    # significantly better than coin-flip, with sane (not wild) calibration.
    # NB: with weak signal, optimal Brier is only ~p(1-p) below 0.25, so a
    # strict Brier bound is not a meaningful assertion at this fold size.
    m = metrics.core_metrics(pred, 5)
    assert m["all"]["accuracy"] > 0.52
    assert m["all"]["acc_binom_pvalue_vs_0.5"] < 1e-3
    assert m["all"]["brier"] < 0.2525
    assert m["nonoverlap"]["n"] < m["all"]["n"]


def test_no_signal_no_confidence():
    """On momentum-free data the pipeline must not hallucinate edge:
    probabilities stay near 0.5 and Brier doesn't blow past chance."""
    X, y, fwd = _xy(momentum=0.0)
    cfg = BacktestConfig(train_days=5, test_days=1, calib_days=1,
                         purge_minutes=30, min_train_rows=2000)
    res = walk_forward(X, y, fwd, 5, lambda: DirectionModel("logistic"),
                       cfg, verbose=False)
    pred = res.predictions
    assert np.abs(pred["p"] - 0.5).mean() < 0.06
    assert metrics.core_metrics(pred, 5)["all"]["brier"] < 0.2525


def test_metrics_tables_run():
    X, y, fwd = _xy(n_days=10)
    cfg = BacktestConfig(train_days=5, test_days=1, calib_days=1,
                         purge_minutes=30, min_train_rows=2000)
    res = walk_forward(X, y, fwd, 5, lambda: DirectionModel("logistic"),
                       cfg, verbose=False)
    cal = metrics.calibration_table(res.predictions)
    assert cal["n"].sum() == len(res.predictions)
    conf = metrics.confident_subsets(res.predictions, 5)
    assert {"threshold", "n", "accuracy"} <= set(conf.columns)


def test_trade_sim_arithmetic():
    # hand-built: p says 0.70 up, quote 0.5, always wins -> pnl = 0.5 - costs
    idx = pd.date_range("2024-01-01", periods=50, freq="5min", tz="UTC")
    pred = pd.DataFrame({"p": 0.70, "y": 1.0, "fwd_ret": 0.001, "fold": 0}, index=idx)
    r = metrics.trade_sim(pred, horizon_min=1, fees=POLYMARKET_FEES,
                          quoted_price=0.5, min_edge=0.05)
    assert r["trades"] == 50
    assert r["hit_rate"] == 1.0
    expected = 0.5 - POLYMARKET_FEES.round_trip_cost(0.5)
    assert abs(r["avg_pnl_per_$1_contract"] - expected) < 1e-9
    # kalshi fees reduce pnl further
    r2 = metrics.trade_sim(pred, horizon_min=1, fees=KALSHI_FEES,
                           quoted_price=0.5, min_edge=0.05)
    assert r2["avg_pnl_per_$1_contract"] < r["avg_pnl_per_$1_contract"]
    # losing case: p says up, never up -> negative pnl, buys NO when p low
    pred_dn = pred.assign(p=0.3)
    r3 = metrics.trade_sim(pred_dn, horizon_min=1, fees=POLYMARKET_FEES,
                           quoted_price=0.5, min_edge=0.05)
    assert r3["hit_rate"] == 0.0
    assert r3["avg_pnl_per_$1_contract"] < 0


def test_fee_models():
    assert KALSHI_FEES.taker_fee(0.5) == 0.07 * 0.25
    assert POLYMARKET_FEES.taker_fee(0.5) == 0.0
    assert POLYMARKET_FEES.round_trip_cost(0.5) == 0.01
