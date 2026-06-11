"""Kronos adapter contract test with an injected fake predictor (no torch)."""

import numpy as np
import pandas as pd

from btc_edge.data import synthetic_bars
from btc_edge.models.kronos_adapter import KronosDirectionModel


class FakePredictor:
    """Mimics KronosPredictor.predict: returns a 1-sample path. Drifts upward
    on 70% of calls so P(up) should land near 0.7."""

    def __init__(self, p_up=0.7, seed=0):
        self.rng = np.random.default_rng(seed)
        self.p_up = p_up
        self.calls = []

    def predict(self, df, x_timestamp, y_timestamp, pred_len, **kw):
        self.calls.append({"pred_len": pred_len, **kw})
        last = float(df["close"].iloc[-1])
        direction = 1.0 if self.rng.random() < self.p_up else -1.0
        closes = last * (1 + direction * 0.0005 * np.arange(1, pred_len + 1))
        return pd.DataFrame({"close": closes})


def test_proba_from_sampled_paths():
    bars = synthetic_bars(n_days=1, seed=2)
    fake = FakePredictor(p_up=1.0)
    m = KronosDirectionModel(predictor=fake, n_samples=10)
    p = m.predict_proba_up(bars, horizon_min=5)
    # all 10 paths up -> laplace-smoothed (10+1)/(10+2)
    assert p == (10 + 1) / (10 + 2)
    call = fake.calls[0]
    assert call["pred_len"] == 5
    assert call["sample_count"] == 1


def test_proba_smoothing_never_extreme():
    bars = synthetic_bars(n_days=1, seed=2)
    m = KronosDirectionModel(predictor=FakePredictor(p_up=0.0), n_samples=8)
    p = m.predict_proba_up(bars, horizon_min=15)
    assert 0.0 < p < 0.2


def test_context_truncated_to_max():
    bars = synthetic_bars(n_days=1, seed=2)

    class CapturingPredictor(FakePredictor):
        def predict(self, df, x_timestamp, y_timestamp, pred_len, **kw):
            assert len(df) <= 512
            assert list(df.columns) == ["open", "high", "low", "close", "volume", "amount"]
            return super().predict(df, x_timestamp, y_timestamp, pred_len, **kw)

    m = KronosDirectionModel(predictor=CapturingPredictor(), n_samples=3, max_context=512)
    m.predict_proba_up(bars, horizon_min=5)
