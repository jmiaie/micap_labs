"""Append-only paper-trade ledger (JSONL) with settlement and summary.

The ledger is the system's source of truth for *realized* edge: every signal
is recorded with the model probability, the venue quote actually seen, and
later the outcome. If live calibration or PnL disagrees with the backtest,
believe the ledger.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..markets.edge import Signal


class Ledger:
    def __init__(self, path: str | Path = "data/ledger/paper.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, sig: Signal) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps({"type": "signal", **sig.to_dict()}) + "\n")

    def settle(self, market_id: str, outcome_yes: bool, settle_price_source: str = "") -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps({
                "type": "settlement",
                "ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "market_id": market_id,
                "outcome_yes": bool(outcome_yes),
                "source": settle_price_source,
            }) + "\n")

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def summary(self) -> dict:
        rows = self.rows()
        signals = [r for r in rows if r["type"] == "signal"]
        outcomes = {r["market_id"]: r["outcome_yes"] for r in rows if r["type"] == "settlement"}
        settled, pnl_sum, wins, brier_sum = 0, 0.0, 0, 0.0
        for s in signals:
            if s["market_id"] not in outcomes:
                continue
            yes = outcomes[s["market_id"]]
            won = yes if s["side"] == "YES" else not yes
            pnl = (1 - s["entry_price"] if won else -s["entry_price"]) - s["fee_est"]
            settled += 1
            wins += int(won)
            pnl_sum += pnl
            brier_sum += (s["model_p"] - (1.0 if yes else 0.0)) ** 2
        return {
            "signals": len(signals),
            "settled": settled,
            "hit_rate": round(wins / settled, 4) if settled else None,
            "avg_pnl_per_$1": round(pnl_sum / settled, 5) if settled else None,
            "model_brier_on_trades": round(brier_sum / settled, 5) if settled else None,
        }
