"""Bot configuration: every knob from PRD §5, with the PRD defaults.

The user-facing originals survive here explicitly:
  - `underdog_price_band` — the 70-85% favorite screen,
  - `max_entry_inning`    — "early in the game",
  - `take_profit_return`  — the +50% automatic take-profit (default ON).

Secrets never live in this file or in JSON configs; they come from the
environment (see `.env.example`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional, Tuple


@dataclass
class BotConfig:
    # --- mode ---------------------------------------------------------------
    mode: str = "paper"                      # backtest | paper | live

    # --- universe screen (PRD 5.1) -------------------------------------------
    underdog_price_band: Tuple[float, float] = (0.15, 0.30)
    max_entry_inning: int = 4
    min_book_depth_usd: float = 50.0
    max_spread: float = 0.04
    allow_favorite_side: bool = True         # take favorite when IT is mispriced

    # --- fair value (PRD 5.2) -------------------------------------------------
    model_weight: float = 1.0                # renormalized with sharp_weight
    sharp_weight: float = 1.0                # ignored when no sharp source
    max_feed_age_s: float = 10.0             # game/book staleness stand-down
    freeze_seconds: float = 45.0             # no trading this long after a score

    # --- entries (PRD 5.3) ----------------------------------------------------
    entry_edge: float = 0.05                 # fair - ask must exceed this
    keep_edge: float = 0.03                  # passive limit at fair - keep_edge
    cross_margin: float = 0.02               # extra edge required to cross spread

    # --- exits (PRD 5.4) --------------------------------------------------------
    take_profit_return: float = 0.50         # +50% cap, per original strategy
    take_profit_enabled: bool = True
    salvage_edge: float = 0.05               # exit if model edge <= -salvage_edge
    exit_edge: float = 0.01                  # converged when bid >= fair - exit_edge
    time_stop_inning: int = 7

    # --- sizing & risk (PRD 5.5) ------------------------------------------------
    bankroll_usd: float = 1000.0
    kelly_fraction: float = 0.25
    max_trade_pct: float = 0.02              # per-trade bankroll cap
    max_depth_participation: float = 0.50    # size <= 50% of displayed touch size
    max_concurrent_positions: int = 5
    max_total_at_risk_pct: float = 0.10
    daily_loss_limit_pct: float = 0.05       # kill switch

    # --- ops ---------------------------------------------------------------------
    poll_seconds: float = 2.0
    fee_bps: float = 0.0                     # verify current Polymarket sports fees
    state_path: str = "rallycap_state.json"
    trade_log_path: str = "rallycap_trades.jsonl"

    # --- secrets (env only; never serialized) --------------------------------------
    polymarket_private_key: Optional[str] = field(default=None, repr=False)
    polymarket_proxy_address: Optional[str] = field(default=None, repr=False)
    odds_api_key: Optional[str] = field(default=None, repr=False)

    _SECRET_FIELDS = ("polymarket_private_key", "polymarket_proxy_address", "odds_api_key")

    def __post_init__(self) -> None:
        lo, hi = self.underdog_price_band
        if not (0.0 < lo < hi < 1.0):
            raise ValueError(f"underdog_price_band must be 0 < lo < hi < 1, got {self.underdog_price_band}")
        if not (0.0 < self.kelly_fraction <= 1.0):
            raise ValueError("kelly_fraction must be in (0, 1]")
        if self.mode not in ("backtest", "paper", "live"):
            raise ValueError(f"unknown mode: {self.mode}")

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: Optional[str] = None) -> "BotConfig":
        """JSON file (if given) overridden by environment for secrets."""
        data = {}
        if path:
            raw = json.loads(Path(path).read_text())
            known = {f.name for f in fields(cls)}
            unknown = set(raw) - known
            if unknown:
                raise ValueError(f"unknown config keys: {sorted(unknown)}")
            if "underdog_price_band" in raw:
                raw["underdog_price_band"] = tuple(raw["underdog_price_band"])
            data = raw
        cfg = cls(**data)
        cfg.polymarket_private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", cfg.polymarket_private_key)
        cfg.polymarket_proxy_address = os.environ.get("POLYMARKET_PROXY_ADDRESS", cfg.polymarket_proxy_address)
        cfg.odds_api_key = os.environ.get("ODDS_API_KEY", cfg.odds_api_key)
        if env_bankroll := os.environ.get("BANKROLL_USD"):
            cfg.bankroll_usd = float(env_bankroll)
        return cfg

    def to_public_dict(self) -> dict:
        """Config without secrets, for logging/state files."""
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        for k in self._SECRET_FIELDS:
            d.pop(k, None)
        return d
