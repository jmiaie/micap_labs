"""Out-of-sample evaluation of the mid-window digital pricer on real bars.

For every wall-clock window [T0, T0+W) and every minute k=1..W-1 into it,
price P(close[T0+W] > close[T0]) from (spot at T0+k, EWMA vol, tau=W-k) and
score against the realized outcome. The tail-shape comparison vs the normal
CDF shows why the empirical Z matters; `fair_move_per_min` is the average
absolute fair-value change per minute — the size of the prize for repricing
faster than resting quotes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from ..models.pricer import ZDistribution, ewma_vol_series, fit_zdists, prob_above


def eval_window_pricer(
    bars_fit: pd.DataFrame,
    bars_eval: pd.DataFrame,
    window_min: int = 5,
    vol_halflife_min: float = 30.0,
) -> dict:
    taus = tuple(range(1, window_min))
    zdists = fit_zdists(bars_fit, taus, kind="empirical", vol_halflife_min=vol_halflife_min)
    normal = ZDistribution()

    close = bars_eval["close"].to_numpy(dtype=float)
    sigma = ewma_vol_series(bars_eval["close"], vol_halflife_min).to_numpy(dtype=float)
    minutes = bars_eval.index.minute.to_numpy()
    n = len(close)

    i0 = np.nonzero((minutes % window_min == 0))[0]
    i0 = i0[(i0 + window_min) < n]
    ref = close[i0]
    y = (close[i0 + window_min] > ref).astype(float)

    rows = []
    p_prev: np.ndarray | None = None
    move_sum, move_n = 0.0, 0
    for k in range(1, window_min):
        tau = window_min - k
        s_now = close[i0 + k]
        sig = sigma[i0 + k]
        ok = np.isfinite(sig) & (sig > 0)
        p_emp = prob_above(s_now[ok], ref[ok], sig[ok], float(tau), zdists[tau])
        p_nrm = prob_above(s_now[ok], ref[ok], sig[ok], float(tau), normal)
        yy = y[ok]
        p_emp_c = np.clip(p_emp, 1e-6, 1 - 1e-6)
        rows.append({
            "minutes_in": k,
            "minutes_left": tau,
            "n": int(ok.sum()),
            "brier_empirical": round(float(np.mean((p_emp - yy) ** 2)), 5),
            "brier_normal": round(float(np.mean((p_nrm - yy) ** 2)), 5),
            "brier_const_0.5": 0.25,
            "log_loss_empirical": round(float(log_loss(yy, p_emp_c, labels=[0, 1])), 5),
            "calib_gap": _max_calib_gap(p_emp, yy),
            "tail_hit_rate_p>0.9": _bucket_rate(p_emp, yy, 0.9),
        })
        if p_prev is not None and len(p_emp) == len(p_prev):
            move_sum += float(np.abs(p_emp - p_prev).sum())
            move_n += len(p_emp)
        p_prev = p_emp

    return {
        "window_min": window_min,
        "per_minute": pd.DataFrame(rows),
        "fair_move_per_min": round(move_sum / max(move_n, 1), 4),
        "n_windows": int(len(i0)),
    }


def _max_calib_gap(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    cut = pd.cut(p, np.linspace(0, 1, bins + 1), include_lowest=True)
    df = pd.DataFrame({"p": p, "y": y, "b": cut}).groupby("b", observed=True).agg(
        n=("y", "size"), pm=("p", "mean"), yr=("y", "mean"))
    df = df[df["n"] >= 50]
    return round(float((df["pm"] - df["yr"]).abs().max()), 4) if len(df) else float("nan")


def _bucket_rate(p: np.ndarray, y: np.ndarray, thr: float) -> float:
    m = p >= thr
    return round(float(y[m].mean()), 4) if m.sum() >= 50 else float("nan")
