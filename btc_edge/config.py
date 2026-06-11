"""Central configuration objects.

Everything that affects money (horizons, fees, sizing caps) lives here so a
backtest and the live runner cannot silently disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Forecast horizons, in minutes. These match the Polymarket 5-minute BTC
# up/down series and Kalshi/Polymarket 15-minute & hourly markets.
HORIZONS_MIN = (5, 15)


@dataclass(frozen=True)
class FeeModel:
    """Cost model for a binary prediction market.

    kalshi_style_fee: if True, per-contract fee = fee_rate * price * (1 - price)
    (Kalshi's published taker fee schedule, fee_rate ~= 0.07 on most series).
    half_spread: assumed half bid/ask spread paid on entry (in probability units).
    Polymarket BTC series currently charge no trading fee; the spread is the cost.
    """

    name: str
    fee_rate: float = 0.0
    kalshi_style_fee: bool = False
    half_spread: float = 0.01

    def taker_fee(self, price: float) -> float:
        if self.kalshi_style_fee:
            return self.fee_rate * price * (1.0 - price)
        return 0.0

    def round_trip_cost(self, price: float) -> float:
        """Total expected cost in probability units for entering at `price` and
        holding to settlement (no exit fee on settlement on either venue)."""
        return self.taker_fee(price) + self.half_spread


KALSHI_FEES = FeeModel(name="kalshi", fee_rate=0.07, kalshi_style_fee=True, half_spread=0.01)
POLYMARKET_FEES = FeeModel(name="polymarket", fee_rate=0.0, kalshi_style_fee=False, half_spread=0.01)


@dataclass(frozen=True)
class SizingConfig:
    kelly_fraction: float = 0.25      # fraction of full Kelly
    max_stake_frac: float = 0.02      # hard cap: fraction of bankroll per trade
    min_edge: float = 0.03            # don't trade unless |p_model - price| - costs >= this
    max_concurrent: int = 3


@dataclass(frozen=True)
class BacktestConfig:
    train_days: int = 45
    test_days: int = 7
    purge_minutes: int = 30           # gap between train end and test start (> max horizon)
    calib_days: int = 5               # tail of each train window used for calibration
    min_train_rows: int = 20_000


@dataclass(frozen=True)
class Paths:
    root: Path = field(default_factory=lambda: Path("data"))

    @property
    def bars(self) -> Path:
        return self.root / "bars"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def ledger(self) -> Path:
        return self.root / "ledger"
