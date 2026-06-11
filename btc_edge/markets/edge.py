"""Edge engine: model probability + venue quote -> trade/no-trade decision.

All prices are probabilities in [0,1] for the YES side. Buying NO at
yes_bid b is equivalent to paying (1-b) for a contract that wins when the
event doesn't happen — handled symmetrically below.

Position sizing is fractional Kelly on the fee-adjusted price with a hard
per-trade cap; both come from SizingConfig and are deliberately conservative.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from ..config import FeeModel, SizingConfig


@dataclass(frozen=True)
class Quote:
    venue: str
    market_id: str
    yes_bid: float
    yes_ask: float
    expiry_utc: datetime | None = None
    meta: dict = field(default_factory=dict)

    @property
    def mid(self) -> float:
        return 0.5 * (self.yes_bid + self.yes_ask)

    @property
    def spread(self) -> float:
        return self.yes_ask - self.yes_bid


@dataclass
class Signal:
    ts_utc: str
    venue: str
    market_id: str
    side: str                 # "YES" | "NO"
    model_p: float            # model P(YES)
    entry_price: float        # price paid for the chosen side
    fee_est: float
    edge_net: float           # model edge after fees, in prob units
    kelly: float
    stake_frac: float         # of bankroll, after fractional Kelly + cap
    expiry_utc: str | None = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class EdgeEngine:
    def __init__(self, fees: FeeModel, sizing: SizingConfig = SizingConfig()):
        self.fees = fees
        self.sizing = sizing

    def evaluate(self, model_p: float, quote: Quote, meta: dict | None = None) -> Signal | None:
        """Return a Signal if either side clears min_edge after costs, else None."""
        if not (0.0 < model_p < 1.0) or quote.yes_ask <= 0 or quote.yes_bid >= 1:
            return None

        # candidate: buy YES at the ask
        yes_price = quote.yes_ask
        yes_fee = self.fees.taker_fee(yes_price)
        yes_edge = model_p - yes_price - yes_fee

        # candidate: buy NO at (1 - bid)
        no_price = 1.0 - quote.yes_bid
        no_fee = self.fees.taker_fee(no_price)
        no_edge = (1.0 - model_p) - no_price - no_fee

        side, price, fee, edge, p_win = (
            ("YES", yes_price, yes_fee, yes_edge, model_p)
            if yes_edge >= no_edge
            else ("NO", no_price, no_fee, no_edge, 1.0 - model_p)
        )
        if edge < self.sizing.min_edge:
            return None

        eff_price = min(price + fee, 1.0 - 1e-9)
        kelly = max(0.0, (p_win - eff_price) / (1.0 - eff_price))
        stake = min(kelly * self.sizing.kelly_fraction, self.sizing.max_stake_frac)
        if stake <= 0:
            return None

        return Signal(
            ts_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            venue=quote.venue,
            market_id=quote.market_id,
            side=side,
            model_p=round(model_p, 4),
            entry_price=round(price, 4),
            fee_est=round(fee, 5),
            edge_net=round(edge, 4),
            kelly=round(kelly, 4),
            stake_frac=round(stake, 5),
            expiry_utc=quote.expiry_utc.isoformat(timespec="seconds") if quote.expiry_utc else None,
            meta={**quote.meta, **(meta or {})},
        )
