#!/usr/bin/env python3
"""Train production artifacts on the most recent data:
  - StackedDirectionModel per horizon (joblib)
  - per-tau empirical Z-distributions for the pricer (json)

Example:
    python scripts/train.py --bars data/bars/btc_1m.parquet --days 60 \
        --horizons 5 15 --out data/artifacts/live
"""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import pandas as pd

from btc_edge.data.schema import validate_bars
from btc_edge.features import build_features, make_labels
from btc_edge.features.labels import aligned_xy
from btc_edge.models import StackedDirectionModel, fit_zdists


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--calib-days", type=int, default=5)
    ap.add_argument("--zdist-days", type=int, default=365)
    ap.add_argument("--horizons", type=int, nargs="+", default=[5, 15])
    ap.add_argument("--out", default="data/artifacts/live")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from btc_edge.data.store import load_bars

    raw = load_bars(args.bars)
    end = raw.index[-1]
    bars = validate_bars(raw.loc[end - pd.Timedelta(days=args.zdist_days):])

    # ---- pricer distributions (long history) ----
    zd = fit_zdists(bars, taus=tuple(range(1, max(args.horizons))))
    (out / "zdists.json").write_text(
        json.dumps({str(t): z.to_dict() for t, z in zd.items()}))
    print(f"fitted {len(zd)} z-distributions on {len(bars):,} bars")

    # ---- direction models (recent window) ----
    recent = bars.loc[end - pd.Timedelta(days=args.days + 2):]
    X_all = build_features(recent)
    labels = make_labels(recent, horizons_min=tuple(args.horizons))
    calib_cut = end - pd.Timedelta(days=args.calib_days)
    for h in args.horizons:
        X, y, _ = aligned_xy(X_all, labels, h)
        tr, cal = X.index < calib_cut, X.index >= calib_cut
        model = StackedDirectionModel().fit(
            X[tr], y[tr].to_numpy(), X[cal], y[cal].to_numpy())
        with open(out / f"model_{h}m.pkl", "wb") as f:
            pickle.dump({"model": model, "features": list(X.columns), "horizon": h,
                         "trained_to": str(end)}, f)
        print(f"h={h}m: trained on {int(tr.sum()):,} rows, "
              f"stacker weights {model.stacker.weights_}")
    print(f"wrote artifacts to {out}/")


if __name__ == "__main__":
    main()
