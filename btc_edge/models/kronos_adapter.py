"""Kronos foundation-model adapter (optional, experimental ensemble member).

Kronos (github.com/shiyu-coder/Kronos) is an open-source foundation model for
financial K-lines. We use it as a probabilistic path sampler: draw N future
OHLCV paths for the next `horizon` bars and estimate
P(up) = P(close[t+h] > close[t]) as the fraction of paths ending higher
(with Laplace smoothing).

Setup (not importable from PyPI):
    git clone https://github.com/shiyu-coder/Kronos /path/to/kronos
    pip install ".[kronos]"
    export KRONOS_REPO=/path/to/kronos
Weights download from HuggingFace on first use (NeoQuasar/Kronos-small,
NeoQuasar/Kronos-Tokenizer-base). CPU works (~seconds per prediction with
small sample counts); a GPU is needed for sample_count >= 30 at live cadence.

Honest caveats: zero-shot Kronos is NOT validated as a 5m BTC edge here —
that's exactly what the walk-forward harness is for. Fine-tune on BTC bars
with Kronos' finetune_csv pipeline before trusting it as a member.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd


class KronosDirectionModel:
    def __init__(
        self,
        model_id: str = "NeoQuasar/Kronos-small",
        tokenizer_id: str = "NeoQuasar/Kronos-Tokenizer-base",
        kronos_repo: str | None = None,
        device: str | None = None,
        max_context: int = 512,
        temperature: float = 0.6,
        top_p: float = 0.9,
        n_samples: int = 30,
        predictor=None,  # injection point for tests
    ):
        self.model_id = model_id
        self.tokenizer_id = tokenizer_id
        self.kronos_repo = kronos_repo or os.environ.get("KRONOS_REPO")
        self.device = device
        self.max_context = max_context
        self.temperature = temperature
        self.top_p = top_p
        self.n_samples = n_samples
        self._predictor = predictor

    # ------------------------------------------------------------------
    def _load(self):
        if self._predictor is not None:
            return self._predictor
        if self.kronos_repo and self.kronos_repo not in sys.path:
            sys.path.insert(0, self.kronos_repo)
        try:
            from model import Kronos, KronosPredictor, KronosTokenizer  # type: ignore
        except ImportError as e:
            raise ImportError(
                "Kronos repo not importable. git clone "
                "https://github.com/shiyu-coder/Kronos and set KRONOS_REPO, "
                "then pip install '.[kronos]'"
            ) from e
        tok = KronosTokenizer.from_pretrained(self.tokenizer_id)
        mdl = Kronos.from_pretrained(self.model_id)
        kwargs = {"max_context": self.max_context}
        if self.device:
            kwargs["device"] = self.device
        self._predictor = KronosPredictor(mdl, tok, **kwargs)
        return self._predictor

    # ------------------------------------------------------------------
    def predict_proba_up(self, bars: pd.DataFrame, horizon_min: int,
                         n_samples: int | None = None) -> float:
        """P(close[t+h] > close[t]) from sampled paths. `bars` are 1m bars
        (canonical schema); the trailing max_context rows form the context."""
        n = n_samples or self.n_samples
        predictor = self._load()

        ctx = bars.tail(self.max_context).copy()
        x_df = pd.DataFrame({
            "open": ctx["open"].astype(float),
            "high": ctx["high"].astype(float),
            "low": ctx["low"].astype(float),
            "close": ctx["close"].astype(float),
            "volume": ctx.get("volume", pd.Series(0.0, index=ctx.index)).astype(float),
        })
        x_df["amount"] = x_df["close"] * x_df["volume"]
        x_ts = pd.Series(ctx.index.tz_localize(None) if ctx.index.tz else ctx.index)
        step = ctx.index[-1] - ctx.index[-2]
        y_ts = pd.Series(pd.date_range(ctx.index[-1] + step, periods=horizon_min, freq=step)
                         .tz_localize(None) if ctx.index.tz else
                         pd.date_range(ctx.index[-1] + step, periods=horizon_min, freq=step))

        s_now = float(ctx["close"].iloc[-1])
        ups = 0
        for _ in range(n):
            pred = predictor.predict(
                x_df, x_ts, y_ts, pred_len=horizon_min,
                T=self.temperature, top_p=self.top_p, sample_count=1, verbose=False,
            )
            ups += int(float(pred["close"].iloc[-1]) > s_now)
        # Laplace smoothing: never returns 0/1 from finite samples
        return (ups + 1.0) / (n + 2.0)
