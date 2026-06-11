import numpy as np
import pandas as pd
import pytest

from btc_edge.data import synthetic_bars, validate_bars
from btc_edge.features import build_features, make_labels, FEATURE_WARMUP_MIN
from btc_edge.features.labels import aligned_xy


@pytest.fixture(scope="module")
def bars():
    return validate_bars(synthetic_bars(n_days=6, seed=3))


def test_features_shape_and_nan(bars):
    f = build_features(bars)
    assert len(f) == len(bars) - FEATURE_WARMUP_MIN
    # after warmup, features should be essentially NaN-free
    assert f.isna().mean().max() < 0.001
    assert f.dtypes.unique().tolist() == [np.dtype("float32")]
    assert f.shape[1] >= 25


def test_no_lookahead(bars):
    """Mutating the future must not change features at or before t."""
    t_cut = bars.index[len(bars) // 2]
    f_full = build_features(bars, drop_warmup=False)

    corrupted = bars.copy()
    after = corrupted.index > t_cut
    corrupted.loc[after, ["open", "high", "low", "close"]] *= 1.37
    corrupted.loc[after, "volume"] *= 9.0
    corrupted.loc[after, "taker_buy_volume"] *= 0.1
    f_corr = build_features(corrupted, drop_warmup=False)

    upto = f_full.index <= t_cut
    pd.testing.assert_frame_equal(f_full[upto], f_corr[upto])


def test_labels_basic():
    idx = pd.date_range("2024-01-01", periods=8, freq="1min", tz="UTC")
    close = pd.Series([100, 101, 99, 99, 105, 104, 103, 110.0], index=idx)
    bars = pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1.0})
    lab = make_labels(bars, horizons_min=(2,))
    # t=0: close[2]=99 < 100 -> 0 ; t=2: close[4]=105 > 99 -> 1
    assert lab["up_2m"].iloc[0] == 0
    assert lab["up_2m"].iloc[2] == 1
    # last 2 rows unknown
    assert lab["up_2m"].iloc[-2:].isna().all()
    # flat forward move counts as NOT up
    close2 = pd.Series([100.0] * 5, index=idx[:5])
    bars2 = pd.DataFrame({"open": close2, "high": close2, "low": close2,
                          "close": close2, "volume": 1.0})
    assert make_labels(bars2, horizons_min=(2,))["up_2m"].iloc[0] == 0


def test_aligned_xy(bars):
    f = build_features(bars)
    lab = make_labels(bars, horizons_min=(5,))
    X, y, fwd = aligned_xy(f, lab, 5)
    assert len(X) == len(y) == len(fwd)
    assert X.index.equals(y.index)
    assert not X.isna().any().any()
    assert set(np.unique(y)) <= {0, 1}
