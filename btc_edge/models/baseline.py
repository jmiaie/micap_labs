"""Supervised direction models: calibrated P(up over next h minutes | features).

Two members:
  'logistic' — standardized L2 logistic regression. Interpretable sanity check.
  'hgb'      — histogram gradient boosting (sklearn-native LightGBM analogue).

Calibration matters more than raw discrimination here: prediction-market PnL
is driven by |p - price| being *honest*, not by AUC. We therefore isotonic-
calibrate on a held-out, time-ordered tail slice that the walk-forward engine
provides (never random splits — time series).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

EPS = 1e-6


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


class DirectionModel:
    def __init__(self, kind: str = "hgb", random_state: int = 0):
        self.kind = kind
        self.random_state = random_state
        self._calibrator = None  # ("isotonic", est) | ("platt", est)
        if kind == "logistic":
            self._est = make_pipeline(
                StandardScaler(),
                LogisticRegression(C=0.2, max_iter=4000, random_state=random_state),
            )
        elif kind == "hgb":
            self._est = HistGradientBoostingClassifier(
                learning_rate=0.05,
                max_iter=400,
                max_leaf_nodes=31,
                min_samples_leaf=200,
                l2_regularization=1.0,
                early_stopping=True,
                validation_fraction=0.15,
                random_state=random_state,
            )
        else:
            raise ValueError(f"unknown model kind: {kind}")

    def fit(self, X: pd.DataFrame, y: np.ndarray,
            X_calib: pd.DataFrame | None = None, y_calib: np.ndarray | None = None) -> "DirectionModel":
        self._est.fit(X, y)
        # 1-min-stride labels overlap, so the effective calibration sample is
        # ~len/h. Isotonic overfits small sets badly; use low-variance Platt
        # scaling below 10k rows, isotonic only when there's real mass.
        if X_calib is not None and len(X_calib) >= 1000:
            raw = self._raw_proba(X_calib)
            if len(X_calib) >= 10_000:
                iso = IsotonicRegression(y_min=0.02, y_max=0.98, out_of_bounds="clip")
                iso.fit(raw, y_calib)
                self._calibrator = ("isotonic", iso)
            else:
                platt = LogisticRegression(C=1e6, max_iter=1000)
                platt.fit(_logit(raw).reshape(-1, 1), y_calib)
                self._calibrator = ("platt", platt)
        return self

    def _raw_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self._est.predict_proba(X)[:, 1]

    def predict_proba_up(self, X: pd.DataFrame) -> np.ndarray:
        p = self._raw_proba(X)
        if self._calibrator is not None:
            kind, est = self._calibrator
            if kind == "isotonic":
                p = est.predict(p)
            else:
                p = est.predict_proba(_logit(p).reshape(-1, 1))[:, 1]
        return np.clip(p, EPS, 1 - EPS)

    def feature_importance(self, X: pd.DataFrame, y: np.ndarray, n_repeats: int = 3) -> pd.Series:
        from sklearn.inspection import permutation_importance

        r = permutation_importance(
            self._est, X, y, n_repeats=n_repeats, random_state=0, scoring="neg_log_loss"
        )
        return pd.Series(r.importances_mean, index=X.columns).sort_values(ascending=False)
