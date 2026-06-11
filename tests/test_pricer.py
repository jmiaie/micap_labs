import numpy as np
import pytest

from btc_edge.data import synthetic_bars
from btc_edge.models.pricer import EwmaVol, ZDistribution, ewma_vol_series, prob_above


def test_prob_above_basics():
    zd = ZDistribution()  # normal
    # at the reference price, 50/50
    assert prob_above(100.0, 100.0, 0.001, 5.0, zd) == pytest.approx(0.5, abs=1e-9)
    # above the reference -> > 0.5, monotone in spot
    p1 = prob_above(100.2, 100.0, 0.001, 5.0, zd)
    p2 = prob_above(100.5, 100.0, 0.001, 5.0, zd)
    assert 0.5 < p1 < p2 < 1.0
    # expiring: indicator
    assert prob_above(100.01, 100.0, 0.001, 0.0, zd) == 1.0
    assert prob_above(99.99, 100.0, 0.001, 0.0, zd) == 0.0
    # more time / more vol pulls toward 0.5
    assert prob_above(100.2, 100.0, 0.001, 60.0, zd) < p1
    assert prob_above(100.2, 100.0, 0.01, 5.0, zd) < p1


def test_prob_above_vectorized():
    p = prob_above(np.array([99.0, 100.0, 101.0]), 100.0, 0.002, 10.0)
    assert p.shape == (3,)
    assert p[0] < 0.5 < p[2]


def test_ewma_vol_consistency():
    bars = synthetic_bars(n_days=2, seed=1)
    sigma_series = ewma_vol_series(bars["close"], 30.0).iloc[-1]
    est = EwmaVol.from_bars(bars, 30.0)
    assert est.sigma == pytest.approx(sigma_series, rel=1e-9)
    # incremental update moves var toward new obs
    before = est.var
    est.update(0.05)
    assert est.var > before


def test_zdistribution_fit_and_roundtrip():
    bars = synthetic_bars(n_days=20, seed=5)
    zd = ZDistribution.fit(bars, horizon_min=5, kind="empirical")
    # CDF is monotone, hits the middle near 0.5, and has fat tails vs normal
    zs = np.linspace(-6, 6, 200)
    cdf = zd.cdf(zs)
    assert np.all(np.diff(cdf) >= -1e-12)
    assert 0.40 < zd.cdf(0.0) < 0.60
    from scipy import stats as st
    assert zd.cdf(-3.5) > st.norm.cdf(-3.5)  # fatter left tail than normal
    # serialization roundtrip
    zd2 = ZDistribution.from_dict(zd.to_dict())
    assert np.allclose(zd2.cdf(zs), cdf)
    # student-t fit also works
    zt = ZDistribution.fit(bars, horizon_min=5, kind="t")
    assert zt.t_df < 30  # fat tails detected
