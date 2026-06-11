"""Purged walk-forward evaluation.

Timeline per fold (all boundaries in time, never row counts):

    [........ train ........][ calib ][ purge ][ test ]
                                       ^ >= max horizon, so no label that
                                         straddles the boundary leaks forward

The model is refit each fold on train, isotonic-calibrated on calib, and
predicts every bar of test. Predictions from all folds are concatenated into
one out-of-sample series — every prediction in the result was made by a model
that never saw its bar's future.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from ..config import BacktestConfig


@dataclass
class WalkForwardResult:
    horizon_min: int
    predictions: pd.DataFrame          # index ts; columns: p, y, fwd_ret, fold
    fold_log: pd.DataFrame
    member_predictions: dict[str, pd.DataFrame] = field(default_factory=dict)


def walk_forward(
    X: pd.DataFrame,
    y: pd.Series,
    fwd_ret: pd.Series,
    horizon_min: int,
    model_factory: Callable[[], object],
    cfg: BacktestConfig = BacktestConfig(),
    verbose: bool = True,
) -> WalkForwardResult:
    """`model_factory()` must return an object with
    fit(X, y, X_calib, y_calib) and predict_proba_up(X)."""
    idx = X.index
    t0, t_end = idx[0], idx[-1]
    train_td = pd.Timedelta(days=cfg.train_days)
    calib_td = pd.Timedelta(days=cfg.calib_days)
    purge_td = pd.Timedelta(minutes=max(cfg.purge_minutes, horizon_min * 2))
    test_td = pd.Timedelta(days=cfg.test_days)

    preds, fold_rows = [], []
    member_preds: dict[str, list] = {}
    fold = 0
    test_start = t0 + train_td + calib_td + purge_td
    while test_start < t_end:
        test_stop = min(test_start + test_td, t_end)
        calib_start = test_start - purge_td - calib_td
        train_mask = (idx >= test_start - purge_td - calib_td - train_td) & (idx < calib_start)
        calib_mask = (idx >= calib_start) & (idx < test_start - purge_td)
        test_mask = (idx >= test_start) & (idx < test_stop)

        if train_mask.sum() < cfg.min_train_rows or test_mask.sum() == 0:
            test_start = test_stop
            continue

        tic = time.time()
        model = model_factory()
        model.fit(X[train_mask], y[train_mask].to_numpy(),
                  X[calib_mask], y[calib_mask].to_numpy())
        p = model.predict_proba_up(X[test_mask])
        preds.append(pd.DataFrame({
            "p": p,
            "y": y[test_mask].to_numpy(),
            "fwd_ret": fwd_ret[test_mask].to_numpy(),
            "fold": fold,
        }, index=idx[test_mask]))
        if hasattr(model, "member_proba"):
            mp = model.member_proba(X[test_mask])
            for name in mp.columns:
                member_preds.setdefault(name, []).append(mp[name])
        fold_rows.append({
            "fold": fold,
            "train_rows": int(train_mask.sum()),
            "test_start": test_start,
            "test_rows": int(test_mask.sum()),
            "fit_seconds": round(time.time() - tic, 1),
        })
        if verbose:
            print(f"[fold {fold:>3}] test {test_start:%Y-%m-%d} +{cfg.test_days}d "
                  f"({fold_rows[-1]['fit_seconds']}s)", flush=True)
        fold += 1
        test_start = test_stop

    if not preds:
        raise ValueError("no folds produced — not enough data for the configured windows")
    all_preds = pd.concat(preds)
    members = {}
    for name, chunks in member_preds.items():
        s = pd.concat(chunks)
        members[name] = all_preds.assign(p=s.to_numpy())
    return WalkForwardResult(
        horizon_min=horizon_min,
        predictions=all_preds,
        fold_log=pd.DataFrame(fold_rows),
        member_predictions=members,
    )
