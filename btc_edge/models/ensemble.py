"""Stacked ensemble: combine member probabilities in logit space.

A logistic regression over member logits, fitted on the calibration slice,
learns both the blend weights and a final calibration shift. With two strong
members this is robust; with one member it degenerates gracefully to a
recalibration of that member.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from .baseline import EPS, _logit


class LogitStacker:
    def __init__(self):
        self._lr = LogisticRegression(C=1.0, max_iter=2000)
        self.members_: list[str] = []

    def fit(self, member_probs: pd.DataFrame, y: np.ndarray) -> "LogitStacker":
        self.members_ = list(member_probs.columns)
        Z = _logit(member_probs.to_numpy(dtype=float))
        self._lr.fit(Z, y)
        return self

    def predict_proba_up(self, member_probs: pd.DataFrame) -> np.ndarray:
        Z = _logit(member_probs[self.members_].to_numpy(dtype=float))
        return np.clip(self._lr.predict_proba(Z)[:, 1], EPS, 1 - EPS)

    @property
    def weights_(self) -> dict:
        return dict(zip(self.members_, self._lr.coef_[0].round(4))) | {
            "intercept": round(float(self._lr.intercept_[0]), 4)
        }


class StackedDirectionModel:
    """Drop-in walk-forward model: fits all members on train, stacks their
    calib-slice probabilities with a logistic meta-learner. Implements the
    same fit/predict_proba_up protocol as DirectionModel, plus member_proba()
    so the engine can log per-member out-of-sample series in one pass.
    """

    def __init__(self, member_kinds=("logistic", "hgb"), random_state: int = 0):
        from .baseline import DirectionModel

        self.members = {k: DirectionModel(k, random_state=random_state) for k in member_kinds}
        self.stacker = LogitStacker()

    def fit(self, X: pd.DataFrame, y: np.ndarray,
            X_calib: pd.DataFrame | None = None, y_calib: np.ndarray | None = None):
        if X_calib is None or len(X_calib) < 1000:
            raise ValueError("StackedDirectionModel requires a calibration slice")
        for m in self.members.values():
            m.fit(X, y)  # members stay uncalibrated; the stacker recalibrates
        self.stacker.fit(self.member_proba(X_calib), y_calib)
        return self

    def member_proba(self, X: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {name: m.predict_proba_up(X) for name, m in self.members.items()}, index=X.index
        )

    def predict_proba_up(self, X: pd.DataFrame) -> np.ndarray:
        return self.stacker.predict_proba_up(self.member_proba(X))
