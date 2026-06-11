"""Fair-value pricer for short-dated binary BTC markets.

The contracts we target reduce to digital options:

  "BTC up or down this 5-min window"  -> pays YES iff S(T1) > S(T0)
  "BTC above K at time T"             -> pays YES iff S(T) > K

Under a driftless diffusion with per-minute vol sigma, with tau minutes left:

  P(S_T > ref) = 1 - F( ln(ref / S_now) / (sigma * sqrt(tau)) )

where F is the CDF of standardized log-returns. BTC 5-15m returns are fat
tailed, so F=Normal misprices the tails (exactly where quotes get interesting).
`ZDistribution` fits an empirical CDF of vol-standardized forward returns from
history and is the recommended F.

This pricer is the core "faster than the market" edge for mid-window trading:
it repricing instantly from the live spot feed while human/market quotes lag.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats


# ---------------------------------------------------------------------------
# volatility
# ---------------------------------------------------------------------------

@dataclass
class EwmaVol:
    """EWMA per-minute vol with O(1) live updates and a vectorized batch fit."""

    halflife_min: float = 30.0
    var: float = float("nan")

    @property
    def _alpha(self) -> float:
        return 1.0 - 0.5 ** (1.0 / self.halflife_min)

    def update(self, log_ret_1m: float) -> float:
        x2 = log_ret_1m * log_ret_1m
        self.var = x2 if math.isnan(self.var) else (1 - self._alpha) * self.var + self._alpha * x2
        return self.sigma

    @property
    def sigma(self) -> float:
        return math.sqrt(self.var) if self.var == self.var else float("nan")

    @classmethod
    def from_bars(cls, bars: pd.DataFrame, halflife_min: float = 30.0) -> "EwmaVol":
        r = np.log(bars["close"]).diff().dropna()
        est = cls(halflife_min=halflife_min)
        est.var = float(r.pow(2).ewm(halflife=halflife_min).mean().iloc[-1])
        return est


def ewma_vol_series(close: pd.Series, halflife_min: float = 30.0) -> pd.Series:
    """Per-minute EWMA vol as a causal series (for backtests)."""
    r = np.log(close).diff()
    return np.sqrt(r.pow(2).ewm(halflife=halflife_min, min_periods=int(halflife_min)).mean())


# ---------------------------------------------------------------------------
# standardized return distribution
# ---------------------------------------------------------------------------

@dataclass
class ZDistribution:
    """Distribution of z = fwd_ret_h / (sigma * sqrt(h)).

    kind: 'normal', 't' (fitted Student-t), or 'empirical' (interpolated CDF).
    Fat tails make 'empirical'/'t' materially better than 'normal' for digital
    pricing at |z| > 1.5.
    """

    kind: str = "normal"
    t_df: float = 4.0
    t_scale: float = 1.0
    q_grid: np.ndarray | None = field(default=None, repr=False)   # quantile values of z
    q_probs: np.ndarray | None = field(default=None, repr=False)  # their CDF levels

    @classmethod
    def fit(cls, bars: pd.DataFrame, horizon_min: int, kind: str = "empirical",
            vol_halflife_min: float = 30.0) -> "ZDistribution":
        logc = np.log(bars["close"].astype(float))
        sig = ewma_vol_series(bars["close"], vol_halflife_min)
        z = ((logc.shift(-horizon_min) - logc) / (sig * np.sqrt(horizon_min))).dropna()
        z = z[np.isfinite(z)]
        if len(z) < 1000:
            raise ValueError(f"not enough data to fit ZDistribution ({len(z)} rows)")
        if kind == "t":
            df_, loc_, scale_ = stats.t.fit(z, floc=0.0)
            return cls(kind="t", t_df=float(df_), t_scale=float(scale_))
        if kind == "empirical":
            probs = np.concatenate([[0.0005, 0.001, 0.0025], np.linspace(0.005, 0.995, 199),
                                    [0.9975, 0.999, 0.9995]])
            grid = np.quantile(z, probs)
            return cls(kind="empirical", q_grid=grid, q_probs=probs)
        return cls(kind="normal")

    def cdf(self, z):
        z = np.asarray(z, dtype=float)
        if self.kind == "t":
            return stats.t.cdf(z, df=self.t_df, loc=0.0, scale=self.t_scale)
        if self.kind == "empirical":
            # linear interp inside the fitted grid, normal tails outside it
            inside = np.interp(z, self.q_grid, self.q_probs)
            lo, hi = self.q_grid[0], self.q_grid[-1]
            out = np.where(z < lo, stats.norm.cdf(z) * (self.q_probs[0] / max(stats.norm.cdf(lo), 1e-12)), inside)
            out = np.where(z > hi, 1 - (1 - stats.norm.cdf(z)) * ((1 - self.q_probs[-1]) / max(1 - stats.norm.cdf(hi), 1e-12)), out)
            return np.clip(out, 0.0, 1.0)
        return stats.norm.cdf(z)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "t_df": self.t_df, "t_scale": self.t_scale,
            "q_grid": None if self.q_grid is None else self.q_grid.tolist(),
            "q_probs": None if self.q_probs is None else self.q_probs.tolist(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ZDistribution":
        return cls(
            kind=d["kind"], t_df=d.get("t_df", 4.0), t_scale=d.get("t_scale", 1.0),
            q_grid=None if d.get("q_grid") is None else np.asarray(d["q_grid"]),
            q_probs=None if d.get("q_probs") is None else np.asarray(d["q_probs"]),
        )


# ---------------------------------------------------------------------------
# pricing
# ---------------------------------------------------------------------------

def prob_above(
    s_now,
    ref_price,
    sigma_per_min,
    minutes_left,
    zdist: ZDistribution | None = None,
) -> np.ndarray | float:
    """P(S at expiry > ref_price). Vectorized over any argument.

    At tau -> 0 collapses to the indicator (s_now > ref): the 'already decided'
    regime where stale market quotes are pure edge.
    """
    zd = zdist or ZDistribution()
    s_now = np.asarray(s_now, dtype=float)
    ref = np.asarray(ref_price, dtype=float)
    sig = np.asarray(sigma_per_min, dtype=float)
    tau = np.asarray(minutes_left, dtype=float)

    denom = sig * np.sqrt(np.maximum(tau, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.log(ref / s_now) / denom
    p = 1.0 - zd.cdf(z)
    decided = (denom <= 0) | ~np.isfinite(z)
    p = np.where(decided, (s_now > ref).astype(float), p)
    return float(p) if p.ndim == 0 else p


def prob_window_up(
    s_now,
    window_open_price,
    sigma_per_min,
    minutes_left,
    zdist: ZDistribution | None = None,
):
    """P(close of window > open of window), mid-window. Identical math with
    ref = window open print (the venue's official open snapshot)."""
    return prob_above(s_now, window_open_price, sigma_per_min, minutes_left, zdist)


def fit_zdists(bars: pd.DataFrame, taus: tuple[int, ...], kind: str = "empirical",
               vol_halflife_min: float = 30.0) -> dict[int, ZDistribution]:
    """One standardized-return distribution per minutes-remaining value, so
    mid-window pricing uses the right tail shape at every tau (short-tau
    returns are much fatter-tailed than the sqrt-time normal scaling implies).
    """
    return {t: ZDistribution.fit(bars, t, kind=kind, vol_halflife_min=vol_halflife_min)
            for t in taus}
