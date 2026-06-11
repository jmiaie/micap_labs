#!/usr/bin/env python3
"""Walk-forward backtest + pricer evaluation on real 1m bars.

Example:
    python scripts/run_backtest.py --bars data/bars/btc_1m.parquet \
        --eval-start 2025-06-01 --eval-end 2026-06-01 \
        --horizons 5 15 --model stacked --out data/artifacts/run1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from btc_edge.backtest import metrics, walk_forward
from btc_edge.backtest.pricer_eval import eval_window_pricer
from btc_edge.config import KALSHI_FEES, POLYMARKET_FEES, BacktestConfig
from btc_edge.data.schema import gap_report, validate_bars
from btc_edge.data.store import load_bars
from btc_edge.features import FEATURE_WARMUP_MIN, build_features, make_labels
from btc_edge.features.labels import aligned_xy
from btc_edge.models import DirectionModel, StackedDirectionModel


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True)
    ap.add_argument("--eval-start", required=True)
    ap.add_argument("--eval-end", required=True)
    ap.add_argument("--fit-years", type=float, default=2.0,
                    help="history before eval-start used for Z-distribution fitting")
    ap.add_argument("--horizons", type=int, nargs="+", default=[5, 15])
    ap.add_argument("--model", choices=["stacked", "hgb", "logistic"], default="stacked")
    ap.add_argument("--train-days", type=int, default=45)
    ap.add_argument("--test-days", type=int, default=7)
    ap.add_argument("--calib-days", type=int, default=5)
    ap.add_argument("--out", default="data/artifacts/backtest")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    eval_start = pd.Timestamp(args.eval_start, tz="UTC")
    eval_end = pd.Timestamp(args.eval_end, tz="UTC")
    fit_start = eval_start - pd.Timedelta(days=int(args.fit_years * 365.25))

    cfg = BacktestConfig(train_days=args.train_days, test_days=args.test_days,
                         calib_days=args.calib_days)
    ml_buffer = pd.Timedelta(days=cfg.train_days + cfg.calib_days + 2, minutes=FEATURE_WARMUP_MIN)

    print(f"loading {args.bars} ...", flush=True)
    raw = load_bars(args.bars)
    bars = validate_bars(raw.loc[fit_start - pd.Timedelta(days=2): eval_end])
    print(json.dumps(gap_report(bars), indent=2), flush=True)

    bars_fit = bars.loc[:eval_start - pd.Timedelta(minutes=1)]
    bars_ml = bars.loc[eval_start - ml_buffer:]
    bars_eval = bars.loc[eval_start - pd.Timedelta(hours=12):]  # vol warmup for pricer

    report: dict = {"config": vars(args), "gap_report": gap_report(bars)}

    # ---------------- ML walk-forward ----------------
    print(f"building features on {len(bars_ml):,} bars ...", flush=True)
    X_all = build_features(bars_ml)
    labels = make_labels(bars_ml, horizons_min=tuple(args.horizons))

    def factory():
        if args.model == "stacked":
            return StackedDirectionModel()
        return DirectionModel(args.model)

    report["ml"] = {}
    for h in args.horizons:
        X, y, fwd = aligned_xy(X_all, labels, h)
        m = (X.index >= eval_start - pd.Timedelta(days=cfg.train_days + cfg.calib_days + 1)) & \
            (X.index <= eval_end)
        X, y, fwd = X[m], y[m], fwd[m]
        print(f"\n=== horizon {h}m: walk-forward on {len(X):,} rows ===", flush=True)
        res = walk_forward(X, y, fwd, h, factory, cfg)
        pred_eval = res.predictions[res.predictions.index >= eval_start]

        sec = {
            "core": metrics.core_metrics(pred_eval, h),
            "confident": metrics.confident_subsets(pred_eval, h).to_dict("records"),
            "calibration": metrics.calibration_table(pred_eval).reset_index().astype(str).to_dict("records"),
            "trade_sim": {
                f"{fees.name}_minedge_{me}": metrics.trade_sim(pred_eval, h, fees, min_edge=me)
                for fees in (POLYMARKET_FEES, KALSHI_FEES) for me in (0.02, 0.03, 0.05)
            },
            "members": {
                name: metrics.core_metrics(mp[mp.index >= eval_start], h)
                for name, mp in res.member_predictions.items()
            },
            "folds": len(res.fold_log),
        }
        report["ml"][f"{h}m"] = sec
        res.predictions.to_parquet(out / f"predictions_{h}m.parquet")
        print(json.dumps(sec["core"], indent=2, default=str), flush=True)

    # ---------------- pricer evaluation ----------------
    report["pricer"] = {}
    for w in args.horizons:
        print(f"\n=== pricer eval, {w}m windows ===", flush=True)
        pe = eval_window_pricer(bars_fit, bars_eval, window_min=w)
        report["pricer"][f"{w}m"] = {
            "n_windows": pe["n_windows"],
            "fair_move_per_min": pe["fair_move_per_min"],
            "per_minute": pe["per_minute"].to_dict("records"),
        }
        print(pe["per_minute"].to_string(index=False), flush=True)
        print("fair_move_per_min:", pe["fair_move_per_min"], flush=True)

    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {out}/report.json", flush=True)


if __name__ == "__main__":
    main()
