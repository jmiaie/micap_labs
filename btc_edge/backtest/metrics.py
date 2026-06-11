"""Honest evaluation of probability forecasts for binary-market trading.

Notes on statistics:
- 1-minute-stride predictions of h-minute outcomes OVERLAP: consecutive labels
  share h-1 minutes, so naive significance tests overstate confidence by ~√h.
  Every test here is therefore also computed on the non-overlapping subsample
  (stride = h), which is the number to believe.
- Accuracy is mostly noise at these horizons; what monetizes is calibration
  plus a usable confident tail. The `confident_subsets` and `trade_sim`
  tables are the decision-relevant outputs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import log_loss, roc_auc_score

from ..config import FeeModel


def core_metrics(pred: pd.DataFrame, horizon_min: int) -> dict:
    out = {}
    for tag, df in (("all", pred), ("nonoverlap", pred.iloc[::horizon_min])):
        p, y = df["p"].to_numpy(), df["y"].to_numpy()
        base = max(y.mean(), 1 - y.mean())
        acc = ((p > 0.5) == y).mean()
        n = len(y)
        k = int((((p > 0.5) == y)).sum())
        binom_p = stats.binomtest(k, n, 0.5, alternative="greater").pvalue if n else np.nan
        out[tag] = {
            "n": n,
            "base_rate": round(float(base), 4),
            "accuracy": round(float(acc), 4),
            "acc_binom_pvalue_vs_0.5": float(f"{binom_p:.2e}") if n else np.nan,
            "brier": round(float(np.mean((p - y) ** 2)), 5),
            "brier_const_0.5": 0.25,
            "log_loss": round(float(log_loss(y, p, labels=[0, 1])), 5),
            "log_loss_const": round(float(log_loss(y, np.full_like(p, y.mean()), labels=[0, 1])), 5),
            "auc": round(float(roc_auc_score(y, p)), 4) if 0 < y.mean() < 1 else np.nan,
        }
    return out


def calibration_table(pred: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    df = pred.copy()
    df["bin"] = pd.cut(df["p"], np.linspace(0, 1, bins + 1), include_lowest=True)
    g = df.groupby("bin", observed=True).agg(n=("y", "size"), p_mean=("p", "mean"), y_rate=("y", "mean"))
    g["gap"] = (g["p_mean"] - g["y_rate"]).round(4)
    return g.round(4)


def confident_subsets(pred: pd.DataFrame, horizon_min: int,
                      thresholds=(0.52, 0.55, 0.58, 0.62)) -> pd.DataFrame:
    """Accuracy when we only act on |p-0.5| >= threshold-0.5 (both sides),
    on the non-overlapping subsample."""
    rows = []
    df = pred.iloc[::horizon_min]
    for thr in thresholds:
        m = (df["p"] >= thr) | (df["p"] <= 1 - thr)
        sub = df[m]
        if len(sub) == 0:
            rows.append({"threshold": thr, "n": 0})
            continue
        side_up = sub["p"] >= 0.5
        correct = np.where(side_up, sub["y"] == 1, sub["y"] == 0)
        k, n = int(correct.sum()), len(sub)
        rows.append({
            "threshold": thr,
            "n": n,
            "share_of_time": round(n / len(df), 4),
            "accuracy": round(k / n, 4),
            "binom_pvalue_vs_0.5": float(f"{stats.binomtest(k, n, 0.5, alternative='greater').pvalue:.2e}"),
            "avg_conf": round(float(np.maximum(sub['p'], 1 - sub['p']).mean()), 4),
        })
    return pd.DataFrame(rows)


def trade_sim(
    pred: pd.DataFrame,
    horizon_min: int,
    fees: FeeModel,
    quoted_price: float = 0.5,
    min_edge: float = 0.03,
    kelly_fraction: float = 0.25,
    max_stake_frac: float = 0.02,
) -> dict:
    """Simulate trading a binary market quoted at `quoted_price` for UP
    (window-open convention: market opens ~50/50) using non-overlapping windows.

    This is deliberately conservative about what we can claim from bars alone:
    it answers "if the venue quotes p0 and we pay the configured spread+fees,
    does acting on our p make money?" Live quote capture (paper trader) is the
    final arbiter of realized edge.

    Windows are aligned to wall-clock boundaries (minute % h == 0), matching
    how the venues actually cut their 5m/15m/hourly markets.
    """
    df = pred[pred.index.minute % horizon_min == 0].copy()
    if len(df) == 0:
        df = pred.iloc[::horizon_min].copy()
    p = df["p"].to_numpy()
    y = df["y"].to_numpy()

    buy_up = p - quoted_price >= min_edge
    buy_dn = (1 - p) - (1 - quoted_price) >= min_edge
    act = buy_up | buy_dn
    if act.sum() == 0:
        return {"trades": 0}

    price = np.where(buy_up, quoted_price, 1 - quoted_price)[act]
    cost = np.array([fees.round_trip_cost(pr) for pr in price])
    win = np.where(buy_up, y == 1, y == 0)[act]
    pnl_per_unit = np.where(win, 1 - price, -price) - cost

    p_act = np.where(buy_up, p, 1 - p)[act]
    eff_price = price + cost
    kelly = np.clip((p_act - eff_price) / np.maximum(1 - eff_price, 1e-9), 0, 1) * kelly_fraction
    stake = np.minimum(kelly, max_stake_frac)

    bankroll = np.cumprod(1 + stake * pnl_per_unit / np.maximum(price, 1e-9))
    n = int(act.sum())
    wins = int(win.sum())
    return {
        "trades": n,
        "share_of_windows": round(n / len(df), 4),
        "hit_rate": round(wins / n, 4),
        "avg_edge_claimed": round(float((p_act - price).mean()), 4),
        "avg_pnl_per_$1_contract": round(float(pnl_per_unit.mean()), 5),
        "total_pnl_per_$1_flat": round(float(pnl_per_unit.sum()), 2),
        "final_bankroll_frac_kelly": round(float(bankroll[-1]), 4),
        "max_drawdown": round(float(1 - (bankroll / np.maximum.accumulate(bankroll)).min()), 4),
        "fee_model": fees.name,
        "assumed_quote": quoted_price,
        "min_edge": min_edge,
    }


def pricer_metrics(p: np.ndarray, y: np.ndarray, label: str) -> dict:
    return {
        "label": label,
        "n": len(y),
        "brier": round(float(np.mean((p - y) ** 2)), 5),
        "log_loss": round(float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6), labels=[0, 1])), 5),
        "calib_gap_max": float(
            calibration_table(pd.DataFrame({"p": p, "y": y}))["gap"].abs().max()
        ),
    }
